import pytest

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
    assert (await create_project(client, actor.token, actor.org_id, "Plan 2")).status_code == 409


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
