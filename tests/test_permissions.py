import pytest
from app.db.base import Invitation, Membership, MembershipRole
from sqlalchemy import select

from tests.helpers import create_project, create_task


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role,method,resource,expected",
    [
        ("member", "get", "projects", 200),
        ("member", "get", "tasks", 200),
        ("member", "post", "projects", 403),
        ("member", "post", "tasks", 403),
        ("member", "patch", "project", 403),
        ("member", "delete", "project", 403),
        ("member", "patch", "task", 403),
        ("member", "delete", "task", 403),
        ("admin", "post", "projects", 201),
        ("owner", "post", "project", 201),
        ("admin", "patch", "project", 200),
        ("admin", "delete", "project", 403),
        ("admin", "post", "tasks", 201),
        ("admin", "delete", "task", 204),
        ("owner", "post", "tasks", 201),
        ("owner", "delete", "project", 204),
    ],
)
async def test_role_matrix_enforces_permissions_on_existing_resources(
    client, user_factory, session_factory, role, method, resource, expected
):
    actor = await user_factory(f"{role}@example.com", "Roles")
    project = (await create_project(client, actor.token, actor.org_id, "Seed project")).json()
    task = (await create_task(client, actor.token, actor.org_id, project["id"], "Seed task")).json()
    async with session_factory() as db:
        membership = await db.scalar(select(Membership).where(Membership.user_id == actor.user_id))
        membership.role = MembershipRole(role)
        await db.commit()

    actions = {
        ("get", "projects"): lambda: client.get("/projects", headers=actor.headers),
        ("get", "tasks"): lambda: client.get("/tasks", headers=actor.headers),
        ("post", "projects"): lambda: create_project(
            client, actor.token, actor.org_id, "New project"
        ),
        ("post", "project"): lambda: create_project(
            client, actor.token, actor.org_id, "New project"
        ),
        ("patch", "project"): lambda: client.patch(
            f"/projects/{project['id']}", headers=actor.headers, json={"name": "Updated"}
        ),
        ("delete", "project"): lambda: client.delete(
            f"/projects/{project['id']}", headers=actor.headers
        ),
        ("post", "tasks"): lambda: create_task(
            client, actor.token, actor.org_id, project["id"], "New task"
        ),
        ("post", "task"): lambda: create_task(
            client, actor.token, actor.org_id, project["id"], "New task"
        ),
        ("patch", "task"): lambda: client.patch(
            f"/tasks/{task['id']}", headers=actor.headers, json={"title": "Updated"}
        ),
        ("delete", "task"): lambda: client.delete(f"/tasks/{task['id']}", headers=actor.headers),
    }
    response = await actions[(method, resource)]()

    assert response.status_code == expected
    projects = await client.get("/projects", headers=actor.headers)
    tasks = await client.get("/tasks", headers=actor.headers)
    project_names = {item["name"] for item in projects.json()["items"]}
    task_titles = {item["title"] for item in tasks.json()["items"]}
    if method == "post" and resource in {"projects", "project"}:
        assert ("New project" in project_names) is (expected == 201)
    if method == "post" and resource in {"tasks", "task"}:
        assert ("New task" in task_titles) is (expected == 201)
    if method == "patch" and resource == "project":
        assert ("Updated" in project_names) is (expected == 200)
        assert ("Seed project" in project_names) is (expected == 403)
    if method == "patch" and resource == "task":
        assert ("Updated" in task_titles) is (expected == 200)
        assert ("Seed task" in task_titles) is (expected == 403)
    if resource == "project" and method == "delete" and expected == 204:
        assert (
            await client.get(f"/projects/{project['id']}", headers=actor.headers)
        ).status_code == 404
        assert (await client.get(f"/tasks/{task['id']}", headers=actor.headers)).status_code == 404
    elif resource == "task" and method == "delete" and expected == 204:
        assert (
            await client.get(f"/projects/{project['id']}", headers=actor.headers)
        ).status_code == 200
        assert (await client.get(f"/tasks/{task['id']}", headers=actor.headers)).status_code == 404
    elif method == "delete" and resource == "project":
        assert (
            await client.get(f"/projects/{project['id']}", headers=actor.headers)
        ).status_code == 200
    elif method == "delete" and resource == "task":
        assert (await client.get(f"/tasks/{task['id']}", headers=actor.headers)).status_code == 200


@pytest.mark.asyncio
async def test_invitation_role_policy_rejects_before_persisting_invitation(
    client, user_factory, session_factory
):
    actor = await user_factory("admin@example.com", "Roles")
    async with session_factory() as db:
        membership = await db.scalar(select(Membership).where(Membership.user_id == actor.user_id))
        membership.role = MembershipRole.ADMIN
        await db.commit()

    allowed = await client.post(
        "/organizations/invite",
        headers=actor.headers,
        json={"email": "member@example.com", "role": "member"},
    )
    denied_admin = await client.post(
        "/organizations/invite",
        headers=actor.headers,
        json={"email": "blocked-admin@example.com", "role": "admin"},
    )
    invalid_role = await client.post(
        "/organizations/invite",
        headers=actor.headers,
        json={"email": "blocked-owner@example.com", "role": "owner"},
    )
    assert allowed.status_code == 202
    assert denied_admin.status_code == 403
    assert invalid_role.status_code == 422

    async with session_factory() as db:
        assert (
            await db.scalar(
                select(Invitation.id).where(Invitation.email == "blocked-admin@example.com")
            )
            is None
        )
        assert (
            await db.scalar(
                select(Invitation.id).where(Invitation.email == "blocked-owner@example.com")
            )
            is None
        )
        membership = await db.scalar(select(Membership).where(Membership.user_id == actor.user_id))
        membership.role = MembershipRole.MEMBER
        await db.commit()

    denied_member = await client.post(
        "/organizations/invite",
        headers=actor.headers,
        json={"email": "blocked-member@example.com", "role": "member"},
    )
    assert denied_member.status_code == 403
    async with session_factory() as db:
        assert (
            await db.scalar(
                select(Invitation.id).where(Invitation.email == "blocked-member@example.com")
            )
            is None
        )
