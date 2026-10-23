import pytest
from sqlalchemy import select, text

from tests.helpers import create_project, create_task


@pytest.mark.asyncio
async def test_project_create_update_and_duplicate_names(client, user_factory):
    actor = await user_factory("owner@example.com", "Workspace")
    created = await create_project(client, actor.token, actor.org_id, "Plan")
    assert created.status_code == 201
    project_id = created.json()["id"]
    assert (await create_project(client, actor.token, actor.org_id, "Plan")).status_code == 409
    renamed = await client.patch(
        f"/projects/{project_id}", headers=actor.headers, json={"name": "Plan 2"}
    )
    assert renamed.status_code == 200 and renamed.json()["name"] == "Plan 2"
    other = await create_project(client, actor.token, actor.org_id, "Taken")
    assert other.status_code == 201
    conflict = await client.patch(
        f"/projects/{project_id}", headers=actor.headers, json={"name": "Taken"}
    )
    assert conflict.status_code == 409
    assert (await client.get(f"/projects/{project_id}", headers=actor.headers)).json()[
        "name"
    ] == "Plan 2"
    assert (await create_project(client, actor.token, actor.org_id, "Plan 2")).status_code == 409


@pytest.mark.asyncio
async def test_whitespace_names_are_rejected_or_normalized(client, user_factory):
    actor = await user_factory("whitespace-project@example.com", "Whitespace")
    assert (await create_project(client, actor.token, actor.org_id, "   ")).status_code == 422
    plan = await create_project(client, actor.token, actor.org_id, " Plan ")
    assert plan.status_code == 201
    assert plan.json()["name"] == "Plan"
    assert (await create_project(client, actor.token, actor.org_id, "Plan")).status_code == 409
    task_title = await client.post(
        "/tasks",
        headers=actor.headers,
        json={"project_id": plan.json()["id"], "title": " ", "description": "description"},
    )
    task_description = await client.post(
        "/tasks",
        headers=actor.headers,
        json={"project_id": plan.json()["id"], "title": "title", "description": "  "},
    )
    extra_field = await client.post(
        "/tasks",
        headers=actor.headers,
        json={
            "project_id": plan.json()["id"],
            "title": "title",
            "description": "description",
            "unexpected": True,
        },
    )
    assert task_title.status_code == task_description.status_code == extra_field.status_code == 422


@pytest.mark.asyncio
async def test_deleting_project_cascades_to_its_tasks(client, user_factory):
    actor = await user_factory("owner@example.com", "Workspace")
    project = (await create_project(client, actor.token, actor.org_id, "Plan")).json()
    task = (await create_task(client, actor.token, actor.org_id, project["id"])).json()
    assert (
        await client.delete(f"/projects/{project['id']}", headers=actor.headers)
    ).status_code == 204
    assert (await client.get(f"/tasks/{task['id']}", headers=actor.headers)).status_code == 404


@pytest.mark.asyncio
async def test_task_patch_rejects_invalid_payloads(client, user_factory):
    actor = await user_factory("owner@example.com", "Workspace")
    project = (await create_project(client, actor.token, actor.org_id)).json()
    task = (
        await create_task(
            client, actor.token, actor.org_id, project["id"], assignee_id=actor.user_id
        )
    ).json()
    assert (
        await client.patch(f"/tasks/{task['id']}", headers=actor.headers, json={})
    ).status_code == 422
    assert (
        await client.patch(f"/tasks/{task['id']}", headers=actor.headers, json={"title": None})
    ).status_code == 422
    assert (
        await client.patch(f"/tasks/{task['id']}", headers=actor.headers, json={"project_id": 123})
    ).status_code == 422
    assert (await client.get(f"/tasks/{task['id']}", headers=actor.headers)).json()[
        "project_id"
    ] == project["id"]


@pytest.mark.asyncio
async def test_task_patch_null_assignee_unassigns_task(client, user_factory):
    actor = await user_factory("owner@example.com", "Workspace")
    project = (await create_project(client, actor.token, actor.org_id)).json()
    task = (
        await create_task(
            client, actor.token, actor.org_id, project["id"], assignee_id=actor.user_id
        )
    ).json()
    updated = await client.patch(
        f"/tasks/{task['id']}", headers=actor.headers, json={"assignee_id": None}
    )
    assert updated.status_code == 200
    assert updated.json()["assignee_id"] is None


@pytest.mark.asyncio
async def test_keyset_pagination_walks_all_tasks_without_duplicates_or_skips(client, user_factory):
    actor = await user_factory("owner@example.com", "Workspace")
    project = (await create_project(client, actor.token, actor.org_id)).json()
    expected = []
    for i in range(5):
        task = await create_task(client, actor.token, actor.org_id, project["id"], f"Task {i}")
        expected.append(task.json()["id"])
    page = await client.get("/tasks?limit=2", headers=actor.headers)
    seen = [item["id"] for item in page.json()["items"]]
    while page.json()["next_cursor"] is not None:
        page = await client.get(
            f"/tasks?limit=2&cursor={page.json()['next_cursor']}", headers=actor.headers
        )
        seen.extend(item["id"] for item in page.json()["items"])
    assert seen == expected
    assert len(seen) == len(set(seen))


@pytest.mark.asyncio
async def test_keyset_cursor_is_none_at_exact_page_boundary(client, user_factory):
    actor = await user_factory("owner@example.com", "Workspace")
    project = (await create_project(client, actor.token, actor.org_id)).json()
    for i in range(2):
        await create_task(client, actor.token, actor.org_id, project["id"], f"Exact {i}")
    page = await client.get("/tasks?limit=2", headers=actor.headers)
    assert len(page.json()["items"]) == 2
    assert page.json()["next_cursor"] is None


@pytest.mark.asyncio
async def test_keyset_pagination_includes_insert_added_between_pages_once(client, user_factory):
    actor = await user_factory("owner@example.com", "Workspace")
    project = (await create_project(client, actor.token, actor.org_id)).json()
    expected = []
    for i in range(4):
        task = await create_task(client, actor.token, actor.org_id, project["id"], f"Before {i}")
        expected.append(task.json()["id"])
    first = await client.get("/tasks?limit=2", headers=actor.headers)
    seen = [item["id"] for item in first.json()["items"]]
    inserted = await create_task(client, actor.token, actor.org_id, project["id"], "Between pages")
    expected.append(inserted.json()["id"])
    cursor = first.json()["next_cursor"]
    while cursor is not None:
        page = await client.get(f"/tasks?limit=2&cursor={cursor}", headers=actor.headers)
        seen.extend(item["id"] for item in page.json()["items"])
        cursor = page.json()["next_cursor"]
    assert seen == expected
    assert len(seen) == len(set(seen))


@pytest.mark.asyncio
async def test_task_filter_and_cursor_work_together(client, user_factory):
    actor = await user_factory("cursor-filter@example.com", "Cursor filter")
    project = (await create_project(client, actor.token, actor.org_id)).json()
    todo_ids = []
    for index in range(4):
        response = await create_task(
            client, actor.token, actor.org_id, project["id"], f"Todo {index}"
        )
        todo_ids.append(response.json()["id"])
    done = (await create_task(client, actor.token, actor.org_id, project["id"], "Done")).json()
    await client.patch(f"/tasks/{done['id']}", headers=actor.headers, json={"status": "done"})

    first = await client.get("/tasks?status=todo&limit=2", headers=actor.headers)
    first_ids = [item["id"] for item in first.json()["items"]]
    assert first_ids == todo_ids[:2]
    second = await client.get(
        f"/tasks?status=todo&limit=2&cursor={first.json()['next_cursor']}",
        headers=actor.headers,
    )
    assert [item["id"] for item in second.json()["items"]] == todo_ids[2:]


@pytest.mark.asyncio
async def test_task_assignment_race_returns_not_found_instead_of_500(
    client, user_factory, session_factory, monkeypatch
):
    import app.services.task as task_service

    from app.db.base import Membership

    owner = await user_factory("task-race-owner@example.com", "Task race")
    member = await user_factory("task-race-member@example.com", "Member home")
    async with session_factory() as db:
        db.add(Membership(user_id=member.user_id, organization_id=owner.org_id))
        await db.commit()
    project = (await create_project(client, owner.token, owner.org_id)).json()
    task = (await create_task(client, owner.token, owner.org_id, project["id"], "Race task")).json()

    async def remove_after_validation(db, organization_id, user_id):
        async with session_factory() as other_session:
            membership = await other_session.scalar(
                select(Membership).where(
                    Membership.organization_id == organization_id,
                    Membership.user_id == user_id,
                )
            )
            await other_session.delete(membership)
            await other_session.commit()

    monkeypatch.setattr(task_service, "_ensure_org_member", remove_after_validation)
    response = await client.patch(
        f"/tasks/{task['id']}",
        headers=owner.headers,
        json={"assignee_id": member.user_id},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["limit=0", "limit=101", "cursor=0"])
async def test_invalid_pagination_parameters_return_422(client, user_factory, query):
    actor = await user_factory("owner@example.com", "Workspace")
    assert (await client.get(f"/tasks?{query}", headers=actor.headers)).status_code == 422


@pytest.mark.asyncio
async def test_assignee_and_unassigned_filters_cannot_be_combined(client, user_factory):
    actor = await user_factory("owner@example.com", "Workspace")
    response = await client.get("/tasks?assignee_id=1&unassigned=true", headers=actor.headers)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_task_filters_match_rows_and_combine(client, user_factory):
    actor = await user_factory("owner@example.com", "Workspace")
    project_a = (await create_project(client, actor.token, actor.org_id, "A")).json()
    project_b = (await create_project(client, actor.token, actor.org_id, "B")).json()
    assigned = (
        await create_task(
            client,
            actor.token,
            actor.org_id,
            project_a["id"],
            "assigned todo",
            assignee_id=actor.user_id,
        )
    ).json()
    done = (await create_task(client, actor.token, actor.org_id, project_a["id"], "done")).json()
    await client.patch(f"/tasks/{done['id']}", headers=actor.headers, json={"status": "done"})
    other_project = (
        await create_task(client, actor.token, actor.org_id, project_b["id"], "other project")
    ).json()
    cases = {
        "status=todo": {assigned["id"], other_project["id"]},
        f"assignee_id={actor.user_id}": {assigned["id"]},
        f"project_id={project_a['id']}": {assigned["id"], done["id"]},
        "unassigned=true": {done["id"], other_project["id"]},
        f"status=todo&project_id={project_a['id']}": {assigned["id"]},
        f"assignee_id={actor.user_id}&project_id={project_a['id']}": {assigned["id"]},
    }
    for query, expected_ids in cases.items():
        response = await client.get(f"/tasks?{query}", headers=actor.headers)
        assert response.status_code == 200
        assert {item["id"] for item in response.json()["items"]} == expected_ids


@pytest.mark.asyncio
async def test_task_filter_indexes_are_selected_by_explain(client, user_factory, session_factory):
    actor = await user_factory("explain@example.com", "Explain")
    project = (await create_project(client, actor.token, actor.org_id, "Index data")).json()
    async with session_factory() as db:
        await db.execute(
            text(
                """INSERT INTO tasks
                    (organization_id, project_id, title, description, status, assignee_id)
                SELECT :org_id, :project_id, 'index-' || n, 'seed',
                       CASE WHEN n <= 5 THEN 'todo'::task_status ELSE 'done'::task_status END,
                       CASE WHEN n = 1 THEN CAST(:user_id AS INTEGER) ELSE NULL END
                FROM generate_series(1, 8000) AS n"""
            ),
            {"org_id": actor.org_id, "project_id": project["id"], "user_id": actor.user_id},
        )
        await db.execute(text("ANALYZE tasks"))
        status_plan = await db.scalar(
            text(
                "EXPLAIN SELECT id FROM tasks WHERE organization_id = :org_id AND status = 'todo'"
            ),
            {"org_id": actor.org_id},
        )
        assignee_plan = await db.scalar(
            text(
                "EXPLAIN SELECT id FROM tasks "
                "WHERE organization_id = :org_id AND assignee_id = :user_id"
            ),
            {"org_id": actor.org_id, "user_id": actor.user_id},
        )
    assert "ix_tasks_organization_status" in status_plan
    assert "ix_tasks_organization_assignee" in assignee_plan
