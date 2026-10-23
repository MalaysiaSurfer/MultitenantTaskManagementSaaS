import re

import pytest
from fastapi.routing import APIRoute


def _dependencies(dependant):
    yield dependant
    for child in dependant.dependencies:
        yield from _dependencies(child)


def _organization_routes():
    from app.api.deps import get_current_membership
    from app.main import app

    routes = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        dependencies = list(_dependencies(route.dependant))
        has_organization_header = any(
            parameter.alias == "X-Organization-ID"
            for dependency in dependencies
            for parameter in dependency.header_params
        )
        if not has_organization_header:
            continue
        assert any(dependency.call is get_current_membership for dependency in dependencies), (
            f"{route.path} declares an organization header without membership validation"
        )
        body = None
        if "projects" in route.path and "project_id" not in route.path and "POST" in route.methods:
            body = {"name": "foreign project"}
        elif route.path == "/tasks" and "POST" in route.methods:
            body = {"project_id": 1, "title": "foreign task", "description": "test"}
        elif route.path == "/tasks/{task_id}" and "PATCH" in route.methods:
            body = {"title": "foreign task"}
        elif route.path == "/organizations/invite":
            body = {"email": "foreign@example.com", "role": "member"}
        elif "/members/" in route.path and "PATCH" in route.methods:
            body = {"role": "member"}
        path = route.path.replace("{project_id}", "1").replace("{task_id}", "1")
        path = path.replace("{user_id}", "1")
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            routes.append((method, path, body))
    return routes


@pytest.mark.parametrize("method,path,body", _organization_routes())
async def test_every_organization_route_hides_foreign_organizations(
    client, user_factory, method, path, body
):
    slug = re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-")
    actor = await user_factory(f"route-guard-{method.lower()}-{slug}-a@example.com", "A")
    foreign = await user_factory(f"route-guard-{method.lower()}-{slug}-b@example.com", "B")
    response = await client.request(
        method,
        path,
        headers={
            "Authorization": f"Bearer {actor.token}",
            "X-Organization-ID": str(foreign.org_id),
        },
        json=body,
    )
    assert response.status_code == 404, f"{method} {path} accepted another organization"
