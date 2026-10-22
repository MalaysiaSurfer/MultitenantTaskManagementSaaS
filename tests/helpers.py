from dataclasses import dataclass

from httpx import AsyncClient

PASSWORD = "TestPassword123!"
TEST_CLIENT_IP = "198.51.100.17"


@dataclass(frozen=True)
class Actor:
    user_id: int
    org_id: int
    token: str
    headers: dict[str, str]


async def register_user(
    client: AsyncClient,
    email: str = "user@example.com",
    organization_name: str = "Org",
):
    return await client.post(
        "/auth/register",
        json={"email": email, "password": PASSWORD, "organization_name": organization_name},
    )


async def login(client: AsyncClient, email: str, password: str = PASSWORD):
    return await client.post("/auth/login", data={"username": email, "password": password})


def auth_headers(token: str, org_id: int | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if org_id is not None:
        headers["X-Organization-ID"] = str(org_id)
    return headers


async def create_project(client: AsyncClient, token: str, org_id: int, name: str = "Project"):
    return await client.post("/projects", headers=auth_headers(token, org_id), json={"name": name})


async def create_task(
    client: AsyncClient,
    token: str,
    org_id: int,
    project_id: int,
    title: str = "Task",
    **extra,
):
    return await client.post(
        "/tasks",
        headers=auth_headers(token, org_id),
        json={"project_id": project_id, "title": title, "description": "Description", **extra},
    )


async def create_actor(
    client: AsyncClient,
    email: str,
    organization_name: str,
) -> Actor:
    registration = await register_user(client, email, organization_name)
    assert registration.status_code == 201, registration.text
    body = registration.json()
    token_response = await login(client, email)
    assert token_response.status_code == 200, token_response.text
    token = token_response.json()["access_token"]
    org_id = body["organization"]["id"]
    return Actor(body["user"]["id"], org_id, token, auth_headers(token, org_id))
