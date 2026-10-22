import os
import subprocess
import sys
from pathlib import Path

import pytest
from app.db.base import Membership, MembershipRole, Organization, Project, Task, TaskStatus, User
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from tests.helpers import auth_headers, create_project, create_task


@pytest.mark.asyncio
async def test_organization_lists_have_positive_controls_and_never_mix_tenants(
    client, user_factory
):
    a = await user_factory("a@example.com", "A")
    b = await user_factory("b@example.com", "B")
    a_project = (await create_project(client, a.token, a.org_id, "A project")).json()
    b_project_1 = (await create_project(client, b.token, b.org_id, "B project 1")).json()
    a_task = (await create_task(client, a.token, a.org_id, a_project["id"], "A task")).json()
    b_task_1 = (await create_task(client, b.token, b.org_id, b_project_1["id"], "B task 1")).json()
    b_project_2 = (await create_project(client, b.token, b.org_id, "B project 2")).json()
    a_project_2 = (await create_project(client, a.token, a.org_id, "A project 2")).json()
    b_task_2 = (await create_task(client, b.token, b.org_id, b_project_2["id"], "B task 2")).json()
    a_task_2 = (await create_task(client, a.token, a.org_id, a_project_2["id"], "A task 2")).json()

    a_projects = await client.get("/projects", headers=a.headers)
    a_tasks = await client.get("/tasks", headers=a.headers)
    assert {item["id"] for item in a_projects.json()["items"]} == {
        a_project["id"],
        a_project_2["id"],
    }
    assert {item["id"] for item in a_tasks.json()["items"]} == {a_task["id"], a_task_2["id"]}

    expected_projects = {b_project_1["id"], b_project_2["id"]}
    expected_tasks = {b_task_1["id"], b_task_2["id"]}
    for endpoint, expected_ids in (("/projects", expected_projects), ("/tasks", expected_tasks)):
        page = await client.get(f"{endpoint}?limit=1", headers=b.headers)
        seen = []
        while True:
            seen.extend(item["id"] for item in page.json()["items"])
            cursor = page.json()["next_cursor"]
            if cursor is None:
                break
            page = await client.get(f"{endpoint}?limit=1&cursor={cursor}", headers=b.headers)
        assert set(seen) == expected_ids
        assert len(seen) == len(expected_ids)


@pytest.mark.asyncio
async def test_foreign_project_and_task_get_match_missing_resource_404(client, user_factory):
    a = await user_factory("a@example.com", "A")
    b = await user_factory("b@example.com", "B")
    project = (await create_project(client, b.token, b.org_id, "private")).json()
    task = (await create_task(client, b.token, b.org_id, project["id"], "private task")).json()
    for path in (f"/projects/{project['id']}", f"/tasks/{task['id']}"):
        foreign = await client.get(path, headers=a.headers)
        missing = await client.get(path.rsplit("/", 1)[0] + "/2147483647", headers=a.headers)
        assert foreign.status_code == missing.status_code == 404
        assert foreign.json() == missing.json() == {"detail": "not found"}


@pytest.mark.asyncio
async def test_foreign_project_task_creation_fails_without_creating_task(
    client, user_factory, session_factory
):
    a = await user_factory("a@example.com", "A")
    b = await user_factory("b@example.com", "B")
    bp = (await create_project(client, b.token, b.org_id)).json()
    response = await create_task(client, a.token, a.org_id, bp["id"], "should not exist")
    assert response.status_code == 404
    async with session_factory() as db:
        assert await db.scalar(select(Task.id).where(Task.title == "should not exist")) is None


@pytest.mark.asyncio
async def test_foreign_assignee_create_and_patch_hide_user_existence_and_leave_no_side_effect(
    client, user_factory, session_factory
):
    a = await user_factory("a@example.com", "A")
    b = await user_factory("b@example.com", "B")
    project = (await create_project(client, a.token, a.org_id)).json()
    task = (await create_task(client, a.token, a.org_id, project["id"], "stable")).json()
    foreign = await create_task(
        client, a.token, a.org_id, project["id"], "bad assignment", assignee_id=b.user_id
    )
    nonexistent = await create_task(
        client, a.token, a.org_id, project["id"], "bad assignment", assignee_id=2147483647
    )
    assert foreign.status_code == nonexistent.status_code == 404
    assert foreign.json() == nonexistent.json()
    async with session_factory() as db:
        assert await db.scalar(select(Task.id).where(Task.title == "bad assignment")) is None

    patched = await client.patch(
        f"/tasks/{task['id']}", headers=a.headers, json={"assignee_id": b.user_id}
    )
    assert patched.status_code == 404
    current = await client.get(f"/tasks/{task['id']}", headers=a.headers)
    assert current.json()["title"] == "stable"
    assert current.json()["assignee_id"] is None


@pytest.mark.asyncio
async def test_foreign_patch_and_delete_return_404_and_leave_objects_alive(client, user_factory):
    a = await user_factory("a@example.com", "A")
    b = await user_factory("b@example.com", "B")
    project = (await create_project(client, b.token, b.org_id)).json()
    task = (await create_task(client, b.token, b.org_id, project["id"])).json()
    assert (
        await client.patch(f"/projects/{project['id']}", headers=a.headers, json={"name": "x"})
    ).status_code == 404
    assert (await client.delete(f"/projects/{project['id']}", headers=a.headers)).status_code == 404
    assert (
        await client.patch(f"/tasks/{task['id']}", headers=a.headers, json={"title": "x"})
    ).status_code == 404
    assert (await client.delete(f"/tasks/{task['id']}", headers=a.headers)).status_code == 404
    assert (await client.get(f"/projects/{project['id']}", headers=b.headers)).json()[
        "name"
    ] == "Project"
    assert (await client.get(f"/tasks/{task['id']}", headers=b.headers)).json()["title"] == "Task"


@pytest.mark.asyncio
async def test_organization_header_hides_foreign_and_missing_org_and_validates_input(
    client, user_factory
):
    a = await user_factory("a@example.com", "A")
    b = await user_factory("b@example.com", "B")
    foreign = await client.get("/projects", headers=auth_headers(a.token, b.org_id))
    missing = await client.get("/projects", headers=auth_headers(a.token, 2147483647))
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()
    assert (
        await client.get("/projects", headers={**auth_headers(a.token), "X-Organization-ID": "abc"})
    ).status_code == 422
    assert (
        await client.get("/projects", headers={**auth_headers(a.token), "X-Organization-ID": "0"})
    ).status_code == 422
    assert (await client.get("/projects", headers=auth_headers(a.token))).status_code == 422


@pytest.mark.asyncio
async def test_deleting_membership_immediately_revokes_previously_working_access(
    client, user_factory, session_factory
):
    actor = await user_factory("member@example.com", "Org")
    assert (await client.get("/projects", headers=actor.headers)).status_code == 200
    async with session_factory() as db:
        membership = await db.scalar(select(Membership).where(Membership.user_id == actor.user_id))
        await db.delete(membership)
        await db.commit()
    assert (await client.get("/projects", headers=actor.headers)).status_code == 404


@pytest.mark.asyncio
async def test_foreign_project_filter_hides_existing_tasks_but_keeps_local_task(
    client, user_factory
):
    a = await user_factory("a@example.com", "A")
    b = await user_factory("b@example.com", "B")
    project_a = (await create_project(client, a.token, a.org_id, "A project")).json()
    project_b = (await create_project(client, b.token, b.org_id, "B project")).json()
    task_a = (await create_task(client, a.token, a.org_id, project_a["id"], "A task")).json()
    await create_task(client, b.token, b.org_id, project_b["id"], "B task")
    foreign = await client.get(f"/tasks?project_id={project_b['id']}", headers=a.headers)
    local = await client.get(f"/tasks?project_id={project_a['id']}", headers=a.headers)
    assert foreign.status_code == 200 and foreign.json()["items"] == []
    assert [item["id"] for item in local.json()["items"]] == [task_a["id"]]


@pytest.mark.asyncio
async def test_same_project_name_is_allowed_in_another_organization(client, user_factory):
    a = await user_factory("a@example.com", "A")
    b = await user_factory("b@example.com", "B")
    assert (await create_project(client, a.token, a.org_id, "Shared name")).status_code == 201
    assert (await create_project(client, b.token, b.org_id, "Shared name")).status_code == 201


@pytest.mark.asyncio
async def test_organizations_list_contains_only_memberships(client, user_factory):
    a = await user_factory("a@example.com", "A")
    await user_factory("b@example.com", "B")
    response = await client.get("/organizations", headers=auth_headers(a.token))
    assert response.status_code == 200
    assert [organization["id"] for organization in response.json()] == [a.org_id]


@pytest.mark.asyncio
async def test_composite_task_project_foreign_key_names_the_tenant_constraint(
    session_factory, clean_database
):
    async with session_factory() as db:
        ua, ub = (
            User(email="a@db.test", hashed_password="x"),
            User(email="b@db.test", hashed_password="x"),
        )
        oa, ob = Organization(name="A"), Organization(name="B")
        db.add_all([ua, ub, oa, ob])
        await db.flush()
        db.add_all(
            [
                Membership(user_id=ua.id, organization_id=oa.id, role=MembershipRole.OWNER),
                Membership(user_id=ub.id, organization_id=ob.id, role=MembershipRole.OWNER),
            ]
        )
        pa, pb = (
            Project(organization_id=oa.id, name="pa"),
            Project(organization_id=ob.id, name="pb"),
        )
        db.add_all([pa, pb])
        await db.flush()
        db.add(
            Task(
                organization_id=oa.id,
                project_id=pa.id,
                title="valid",
                description="x",
                status=TaskStatus.TODO,
            )
        )
        await db.commit()
        db.add(
            Task(
                organization_id=oa.id,
                project_id=pb.id,
                title="invalid",
                description="x",
                status=TaskStatus.TODO,
            )
        )
        with pytest.raises(IntegrityError) as exc:
            await db.commit()
        assert "tasks_project_id_organization_id_fkey" in str(exc.value.orig)


@pytest.mark.asyncio
async def test_migrations_created_enums_partial_invitation_index_and_composite_fk(test_engine):
    async with test_engine.connect() as conn:
        enums = (
            (
                await conn.execute(
                    text(
                        "SELECT typname FROM pg_type WHERE typname IN "
                        "('membership_role', 'invitation_role', 'task_status')"
                    )
                )
            )
            .scalars()
            .all()
        )
        indexes = (
            (
                await conn.execute(
                    text(
                        "SELECT indexdef FROM pg_indexes WHERE tablename = 'invitations' "
                        "AND indexname = 'uq_invitations_pending_org_email'"
                    )
                )
            )
            .scalars()
            .all()
        )
        foreign_keys = (
            (
                await conn.execute(
                    text(
                        "SELECT conname FROM pg_constraint WHERE conrelid = 'tasks'::regclass "
                        "AND conname = 'tasks_project_id_organization_id_fkey'"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert set(enums) == {"membership_role", "invitation_role", "task_status"}
    assert len(indexes) == 1 and "WHERE (accepted_at IS NULL)" in indexes[0]
    assert foreign_keys == ["tasks_project_id_organization_id_fkey"]


def test_alembic_migrations_match_current_models(database_url):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "check"],
        cwd=root,
        env=os.environ | {"DATABASE_URL": database_url},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
