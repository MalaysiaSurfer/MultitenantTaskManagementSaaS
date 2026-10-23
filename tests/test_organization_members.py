import pytest
from app.db.base import Membership, MembershipRole, Task
from sqlalchemy.exc import IntegrityError

from tests.helpers import auth_headers, create_project, create_task


async def add_to_organization(session_factory, actor, organization_id, role=MembershipRole.MEMBER):
    async with session_factory() as db:
        db.add(Membership(user_id=actor.user_id, organization_id=organization_id, role=role))
        await db.commit()


@pytest.mark.asyncio
async def test_member_list_role_change_remove_and_assignee_nulling(
    client, user_factory, session_factory
):
    owner = await user_factory("owner-members@example.com", "Members")
    member = await user_factory("member-members@example.com", "Other org")
    outsider = await user_factory("outsider-members@example.com", "No membership")
    await add_to_organization(session_factory, member, owner.org_id)

    listed = await client.get("/organizations/members", headers=owner.headers)
    assert listed.status_code == 200
    assert {row["email"]: row["role"] for row in listed.json()["items"]} == {
        "owner-members@example.com": "owner",
        "member-members@example.com": "member",
    }
    first_page = await client.get("/organizations/members?limit=1", headers=owner.headers)
    assert len(first_page.json()["items"]) == 1
    next_page = await client.get(
        f"/organizations/members?limit=1&cursor={first_page.json()['next_cursor']}",
        headers=owner.headers,
    )
    assert len(next_page.json()["items"]) == 1
    assert next_page.json()["next_cursor"] is None

    changed = await client.patch(
        f"/organizations/members/{member.user_id}",
        headers=owner.headers,
        json={"role": "admin"},
    )
    assert changed.status_code == 200
    assert changed.json()["role"] == "admin"
    promoted = await client.patch(
        f"/organizations/members/{member.user_id}",
        headers=owner.headers,
        json={"role": "owner"},
    )
    assert promoted.status_code == 200
    assert promoted.json()["role"] == "owner"

    project = (await create_project(client, owner.token, owner.org_id, "Assignments")).json()
    async with session_factory() as db:
        db.add(
            Task(
                organization_id=owner.org_id,
                project_id=project["id"],
                title="Invalid cross-organization assignment",
                description="direct database write",
                assignee_id=outsider.user_id,
            )
        )
        with pytest.raises(IntegrityError):
            await db.commit()
        await db.rollback()
    task = (
        await create_task(
            client,
            owner.token,
            owner.org_id,
            project["id"],
            "Assigned task",
            assignee_id=member.user_id,
        )
    ).json()
    removed = await client.delete(f"/organizations/members/{member.user_id}", headers=owner.headers)
    assert removed.status_code == 204
    task_response = await client.get(f"/tasks/{task['id']}", headers=owner.headers)
    assert task_response.status_code == 200
    assert task_response.json()["assignee_id"] is None
    assert (
        await client.get(
            "/organizations/members",
            headers=auth_headers(member.token, owner.org_id),
        )
    ).status_code == 404


@pytest.mark.asyncio
async def test_owner_cannot_leave_or_be_demoted_as_last_owner(client, user_factory):
    owner = await user_factory("last-owner@example.com", "Owned")
    leave = await client.delete("/organizations/members/me", headers=owner.headers)
    assert leave.status_code == 409
    demote = await client.patch(
        f"/organizations/members/{owner.user_id}",
        headers=owner.headers,
        json={"role": "member"},
    )
    assert demote.status_code == 409
    assert (await client.get("/organizations/members", headers=owner.headers)).json()["items"][0][
        "role"
    ] == "owner"


@pytest.mark.asyncio
async def test_organization_create_rename_and_delete(client, user_factory):
    owner = await user_factory("organization-crud@example.com", "Home")
    created = await client.post("/organizations", headers=owner.headers, json={"name": " Extra "})
    assert created.status_code == 201
    assert created.json()["name"] == "Extra"
    assert created.json()["role"] == "owner"
    organization_id = created.json()["id"]

    listed = await client.get("/organizations", headers=auth_headers(owner.token))
    assert {row["id"] for row in listed.json()} == {owner.org_id, organization_id}
    context_headers = auth_headers(owner.token, organization_id)
    renamed = await client.patch(
        "/organizations", headers=context_headers, json={"name": "Renamed"}
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Renamed"
    deleted = await client.delete("/organizations", headers=context_headers)
    assert deleted.status_code == 204
    assert (await client.get("/projects", headers=context_headers)).status_code == 404
    listed = await client.get("/organizations", headers=auth_headers(owner.token))
    assert {row["id"] for row in listed.json()} == {owner.org_id}


@pytest.mark.asyncio
async def test_member_can_leave_and_admin_cannot_manage_roles(
    client, user_factory, session_factory
):
    owner = await user_factory("owner-leave@example.com", "Leave")
    member = await user_factory("member-leave@example.com", "Separate")
    await add_to_organization(session_factory, member, owner.org_id)
    left = await client.delete(
        "/organizations/members/me", headers=auth_headers(member.token, owner.org_id)
    )
    assert left.status_code == 204

    admin = await user_factory("admin-leave@example.com", "Admin separate")
    await add_to_organization(session_factory, admin, owner.org_id, MembershipRole.ADMIN)
    target = await user_factory("target-leave@example.com", "Target")
    await add_to_organization(session_factory, target, owner.org_id)
    role_change = await client.patch(
        f"/organizations/members/{target.user_id}",
        headers=auth_headers(admin.token, owner.org_id),
        json={"role": "admin"},
    )
    assert role_change.status_code == 403
    assert (
        await client.delete(
            f"/organizations/members/{owner.user_id}",
            headers=auth_headers(admin.token, owner.org_id),
        )
    ).status_code == 403
