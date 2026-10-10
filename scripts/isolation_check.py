import asyncio
import os
import sys
import uuid
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

import httpx
from app.core.config import settings
from app.core.constants import INT4_MAX
from app.db.base import Membership, MembershipRole, Task
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    async_sessionmaker,
    create_async_engine,
)

DEFAULT_DATABASE_URL = str(settings.database_url).replace(
    "@db:",
    "@localhost:",
)
DATABASE_URL = os.getenv("TEST_DATABASE_URL", DEFAULT_DATABASE_URL)

COMPOSITE_FK_NAME = "tasks_project_id_organization_id_fkey"

test_engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(
    test_engine,
    expire_on_commit=False,
)

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
PASSWORD = os.getenv("TEST_PASSWORD", "TestPassword123!")

RUN_ID = uuid.uuid4().hex[:8]

EMAIL_A = f"isolation-a-{RUN_ID}@example.com"
EMAIL_B = f"isolation-b-{RUN_ID}@example.com"
EMAIL_ADMIN = f"isolation-admin-{RUN_ID}@example.com"
EMAIL_MEMBER = f"isolation-member-{RUN_ID}@example.com"

ORG_NAME_A = f"Isolation Org A {RUN_ID}"
ORG_NAME_B = f"Isolation Org B {RUN_ID}"
ORG_NAME_ADMIN = f"Isolation Admin Org {RUN_ID}"
ORG_NAME_MEMBER = f"Isolation Member Org {RUN_ID}"

PROJECT_NAME_A = f"Project A {RUN_ID}"
PROJECT_NAME_B = f"Project B {RUN_ID}"

results: list[tuple[str, bool]] = []


def check(
    name: str,
    passed: bool,
    *,
    response: httpx.Response | None = None,
    details: str = "",
) -> bool:
    """Выводит результат проверки и сохраняет его для итогового счёта."""

    results.append((name, passed))

    mark = "PASS" if passed else "FAIL"
    print(f"[{mark}] {name}")

    if not passed:
        if response is not None:
            print(f"       HTTP {response.status_code}")
            print(f"       Body: {response.text}")
        if details:
            print(f"       Details: {details}")
    elif details:
        print(f"       {details}")

    return passed


def json_body(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def extract_id(data: Any) -> int | str | None:
    if not isinstance(data, dict):
        return None

    if data.get("id") is not None:
        return data["id"]

    for key in ("user", "organization", "project", "task", "data"):
        nested = data.get(key)
        result = extract_id(nested)
        if result is not None:
            return result

    return None


def extract_named_id(data: Any, name: str) -> int | str | None:
    """Ищет ID организации по её имени."""

    if isinstance(data, list):
        for item in data:
            result = extract_named_id(item, name)
            if result is not None:
                return result

    elif isinstance(data, dict):
        if data.get("name") == name and data.get("id") is not None:
            return data["id"]

        for value in data.values():
            if isinstance(value, (dict, list)):
                result = extract_named_id(value, name)
                if result is not None:
                    return result

    return None


def contains_task(data: Any, title: str) -> bool:
    """Проверяет наличие задачи по названию в ответе GET /tasks."""

    if isinstance(data, list):
        return any(contains_task(item, title) for item in data)

    if isinstance(data, dict):
        if data.get("title") == title:
            return True

        return any(
            contains_task(value, title)
            for value in data.values()
            if isinstance(value, (dict, list))
        )

    return False


async def register(
    client: httpx.AsyncClient,
    email: str,
    organization_name: str,
) -> tuple[int | str, str]:
    response = await client.post(
        "/auth/register",
        json={
            "email": email,
            "password": PASSWORD,
            "organization_name": organization_name,
        },
    )

    if not check(
        f"Register {email}",
        response.status_code in (200, 201),
        response=response,
    ):
        raise RuntimeError(f"Registration failed for {email}")

    data = json_body(response)
    user_id = extract_id(data.get("user")) if isinstance(data, dict) else None

    if user_id is None:
        raise RuntimeError(f"Registration response has no user.id: {response.text}")

    return user_id, email


async def login(
    client: httpx.AsyncClient,
    email: str,
) -> str:
    response = await client.post(
        "/auth/login",
        data={
            "username": email,
            "password": PASSWORD,
        },
    )

    if not check(
        f"Login {email}",
        response.status_code == 200,
        response=response,
    ):
        raise RuntimeError(f"Login failed for {email}")

    data = json_body(response)
    token = data.get("access_token") if isinstance(data, dict) else None

    if not token:
        raise RuntimeError(f"Login response has no access_token: {response.text}")

    return token


async def get_organization_id(
    client: httpx.AsyncClient,
    token: str,
    organization_name: str,
) -> int | str:
    response = await client.get(
        "/organizations",
        headers={"Authorization": f"Bearer {token}"},
    )

    if not check(
        f"Get organization ID: {organization_name}",
        response.status_code == 200,
        response=response,
    ):
        raise RuntimeError("Cannot retrieve organizations")

    organization_id = extract_named_id(
        json_body(response),
        organization_name,
    )

    if organization_id is None:
        raise RuntimeError(f"Organization {organization_name!r} not found: {response.text}")

    return organization_id


def org_headers(
    token: str,
    organization_id: int | str,
) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Organization-ID": str(organization_id),
    }


async def create_project(
    client: httpx.AsyncClient,
    token: str,
    organization_id: int | str,
    name: str,
) -> httpx.Response:
    return await client.post(
        "/projects",
        headers=org_headers(token, organization_id),
        json={"name": name},
    )


async def create_task(
    client: httpx.AsyncClient,
    token: str,
    organization_id: int | str,
    project_id: int | str,
    title: str,
    assignee_id: int | str | None = None,
) -> httpx.Response:
    payload: dict[str, Any] = {
        "project_id": project_id,
        "title": title,
        "description": "Isolation acceptance test",
    }

    if assignee_id is not None:
        payload["assignee_id"] = assignee_id

    return await client.post(
        "/tasks",
        headers=org_headers(token, organization_id),
        json=payload,
    )


async def list_tasks(
    client: httpx.AsyncClient,
    token: str,
    organization_id: int | str,
) -> httpx.Response:
    return await client.get(
        "/tasks",
        headers=org_headers(token, organization_id),
    )


async def insert_membership(
    user_id: int | str,
    organization_id: int | str,
    role: MembershipRole,
) -> None:
    async with async_session() as session:
        membership = Membership(
            user_id=user_id,
            organization_id=organization_id,
            role=role,
        )
        session.add(membership)
        await session.commit()


async def remove_membership(
    user_id: int | str,
    organization_id: int | str,
) -> None:
    async with async_session() as session:
        result = await session.execute(
            select(Membership).where(
                Membership.user_id == user_id,
                Membership.organization_id == organization_id,
            )
        )
        membership = result.scalar_one_or_none()

        if membership is not None:
            await session.delete(membership)
            await session.commit()


async def check_composite_fk(
    invalid_organization_id: int,
    invalid_project_id: int,
    valid_organization_id: int,
    valid_project_id: int,
) -> tuple[bool, str]:
    """Проверяет составной FK на несовпадающей и совпадающей парах."""

    async with async_session() as session:
        invalid_task = Task(
            organization_id=invalid_organization_id,
            project_id=invalid_project_id,
            title=f"Invalid FK {uuid.uuid4().hex[:8]}",
            description="Composite foreign key negative control",
        )
        session.add(invalid_task)

        try:
            await session.flush()
        except IntegrityError as exc:
            error_text = str(exc.orig)
            await session.rollback()

            if COMPOSITE_FK_NAME not in error_text:
                return (
                    False,
                    f"Expected violation of {COMPOSITE_FK_NAME}, got: {error_text}",
                )
        except Exception:
            await session.rollback()
            raise
        else:
            await session.rollback()
            return (
                False,
                f"Invalid pair was accepted; expected violation of {COMPOSITE_FK_NAME}",
            )

    async with async_session() as session:
        valid_task = Task(
            organization_id=valid_organization_id,
            project_id=valid_project_id,
            title=f"Valid FK {uuid.uuid4().hex[:8]}",
            description="Composite foreign key positive control",
        )
        session.add(valid_task)

        try:
            await session.flush()
        except Exception as exc:
            await session.rollback()
            return (
                False,
                f"Valid pair was rejected: {exc}",
            )
        else:
            await session.rollback()

    return (
        True,
        f"Invalid pair rejected by {COMPOSITE_FK_NAME}; matching pair accepted",
    )


async def main() -> int:
    print(f"Base URL: {BASE_URL}")
    print(f"Database: {DATABASE_URL.rsplit('@', 1)[-1]}")
    print(f"Run ID: {RUN_ID}")
    print()

    try:
        async with httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=httpx.Timeout(15.0),
        ) as client:
            # 1. Создание пользователей.

            await register(client, EMAIL_A, ORG_NAME_A)
            user_b_id, _ = await register(client, EMAIL_B, ORG_NAME_B)
            admin_id, _ = await register(
                client,
                EMAIL_ADMIN,
                ORG_NAME_ADMIN,
            )
            member_id, _ = await register(
                client,
                EMAIL_MEMBER,
                ORG_NAME_MEMBER,
            )

            token_a = await login(client, EMAIL_A)
            token_b = await login(client, EMAIL_B)
            token_admin = await login(client, EMAIL_ADMIN)
            token_member = await login(client, EMAIL_MEMBER)

            org_a_id = await get_organization_id(
                client,
                token_a,
                ORG_NAME_A,
            )
            org_b_id = await get_organization_id(
                client,
                token_b,
                ORG_NAME_B,
            )

            await insert_membership(
                admin_id,
                org_a_id,
                MembershipRole.ADMIN,
            )
            await insert_membership(
                member_id,
                org_a_id,
                MembershipRole.MEMBER,
            )

            headers_a = org_headers(token_a, org_a_id)
            headers_b = org_headers(token_b, org_b_id)
            headers_admin = org_headers(token_admin, org_a_id)
            headers_member = org_headers(token_member, org_a_id)

            # 2. Создание проектов.

            project_a_response = await create_project(
                client,
                token_a,
                org_a_id,
                PROJECT_NAME_A,
            )

            if not check(
                "Create project A",
                project_a_response.status_code == 201,
                response=project_a_response,
            ):
                return 1

            project_b_response = await create_project(
                client,
                token_b,
                org_b_id,
                PROJECT_NAME_B,
            )

            if not check(
                "Create project B",
                project_b_response.status_code == 201,
                response=project_b_response,
            ):
                return 1

            project_a_id = extract_id(json_body(project_a_response))
            project_b_id = extract_id(json_body(project_b_response))

            if project_a_id is None or project_b_id is None:
                raise RuntimeError("Could not extract project IDs")

            # 3. Позитивные проверки ролей.

            response = await client.get(
                "/projects",
                headers=headers_member,
            )
            check(
                "MEMBER GET /projects -> 200",
                response.status_code == 200,
                response=response,
            )

            response = await create_project(
                client,
                token_admin,
                org_a_id,
                f"Admin Project {RUN_ID}",
            )
            check(
                "ADMIN POST /projects -> 201",
                response.status_code == 201,
                response=response,
            )

            projects_a_response = await client.get(
                "/projects",
                headers=headers_a,
            )

            check(
                "GET /projects as A: Project A is visible",
                (
                    projects_a_response.status_code == 200
                    and extract_named_id(
                        json_body(projects_a_response),
                        PROJECT_NAME_A,
                    )
                    is not None
                ),
                response=projects_a_response,
            )

            check(
                "GET /projects as A: Project B is not visible",
                (
                    projects_a_response.status_code == 200
                    and extract_named_id(
                        json_body(projects_a_response),
                        PROJECT_NAME_B,
                    )
                    is None
                ),
                response=projects_a_response,
            )

            # 4. Создание задач.

            task_a_title = f"Task A {RUN_ID}"
            task_b_title = f"Task B {RUN_ID}"

            task_a_response = await create_task(
                client,
                token_a,
                org_a_id,
                project_a_id,
                task_a_title,
            )

            if not check(
                "Create task A without assignee",
                task_a_response.status_code in (200, 201),
                response=task_a_response,
            ):
                return 1

            task_a_id = extract_id(json_body(task_a_response))

            if task_a_id is None:
                raise RuntimeError("Could not extract task A ID")

            task_b_response = await create_task(
                client,
                token_b,
                org_b_id,
                project_b_id,
                task_b_title,
            )

            if not check(
                "Create task B without assignee",
                task_b_response.status_code in (200, 201),
                response=task_b_response,
            ):
                return 1

            task_b_id = extract_id(json_body(task_b_response))

            if task_b_id is None:
                raise RuntimeError("Could not extract task B ID")

            # 5. Позитивная проверка поиска задач.

            tasks_response = await list_tasks(
                client,
                token_a,
                org_a_id,
            )

            task_a_found = tasks_response.status_code == 200 and contains_task(
                json_body(tasks_response), task_a_title
            )

            if not check(
                "Positive control: Task A exists",
                task_a_found,
                response=tasks_response,
            ):
                return 1

            tasks_a_response = await list_tasks(
                client,
                token_a,
                org_a_id,
            )

            check(
                "GET /tasks as A: Task B is not visible",
                (
                    tasks_a_response.status_code == 200
                    and not contains_task(
                        json_body(tasks_a_response),
                        task_b_title,
                    )
                ),
                response=tasks_a_response,
            )

            tasks_b_response = await list_tasks(
                client,
                token_b,
                org_b_id,
            )

            check(
                "GET /tasks as B: Task A is not visible",
                (
                    tasks_b_response.status_code == 200
                    and not contains_task(
                        json_body(tasks_b_response),
                        task_a_title,
                    )
                ),
                response=tasks_b_response,
            )

            # 6. Изоляция чтения.

            response = await client.get(
                f"/projects/{project_b_id}",
                headers=headers_a,
            )
            check(
                "A GET project B -> 404",
                response.status_code == 404,
                response=response,
            )

            cross_task_response = await client.get(
                f"/tasks/{task_b_id}",
                headers=headers_a,
            )
            check(
                "A GET task B -> 404",
                cross_task_response.status_code == 404,
                response=cross_task_response,
            )

            missing_task_response = await client.get(
                "/tasks/2147483647",
                headers=headers_a,
            )

            check(
                "Cross-org task 404 body matches missing task 404",
                (
                    cross_task_response.status_code == 404
                    and missing_task_response.status_code == 404
                    and json_body(cross_task_response) == json_body(missing_task_response)
                ),
                response=cross_task_response,
                details=(
                    "Missing-resource response: "
                    f"HTTP {missing_task_response.status_code}, "
                    f"body={missing_task_response.text}"
                ),
            )

            # 7. Попытка создать задачу в чужом проекте.

            cross_title = f"Must not exist {RUN_ID}"

            response = await create_task(
                client,
                token_a,
                org_a_id,
                project_b_id,
                cross_title,
            )

            cross_create_returned_404 = check(
                "A POST task with project_id B -> 404",
                response.status_code == 404,
                response=response,
            )

            if cross_create_returned_404:
                tasks_response = await list_tasks(
                    client,
                    token_a,
                    org_a_id,
                )

                found = tasks_response.status_code == 200 and contains_task(
                    json_body(tasks_response), cross_title
                )

                check(
                    "Cross-org task was not created",
                    tasks_response.status_code == 200 and not found,
                    response=tasks_response,
                )
            else:
                check(
                    "Cross-org task was not created",
                    False,
                    details="Cross-org POST did not return 404",
                )

            # 8. Попытка назначить исполнителя из другой организации.

            cross_assignee_title = f"Invalid assignee {RUN_ID}"

            response = await create_task(
                client,
                token_a,
                org_a_id,
                project_a_id,
                cross_assignee_title,
                assignee_id=user_b_id,
            )

            assignee_create_returned_404 = check(
                "A POST task with assignee_id B -> 404",
                response.status_code == 404,
                response=response,
            )

            if assignee_create_returned_404:
                tasks_response = await list_tasks(
                    client,
                    token_a,
                    org_a_id,
                )

                found = tasks_response.status_code == 200 and contains_task(
                    json_body(tasks_response),
                    cross_assignee_title,
                )

                check(
                    "Task with cross-org assignee was not created",
                    tasks_response.status_code == 200 and not found,
                    response=tasks_response,
                )
            else:
                check(
                    "Task with cross-org assignee was not created",
                    False,
                    details="Cross-assignee POST did not return 404",
                )

            # 9. Дубликат имени проекта.

            response = await create_project(
                client,
                token_a,
                org_a_id,
                PROJECT_NAME_A,
            )
            check(
                "Duplicate project name -> 409",
                response.status_code == 409,
                response=response,
            )

            # 10. Проверка запретов ролей.

            response = await create_project(
                client,
                token_member,
                org_a_id,
                f"Member forbidden {RUN_ID}",
            )
            check(
                "MEMBER POST /projects -> 403",
                response.status_code == 403,
                response=response,
            )

            response = await client.delete(
                f"/projects/{project_a_id}",
                headers=headers_admin,
            )
            check(
                "ADMIN DELETE /projects/{id} -> 403",
                response.status_code == 403,
                response=response,
            )

            # 11. Проверка X-Organization-ID.
            # Один HTTP-запрос используется в двух проверках.

            foreign_org_response = await client.get(
                "/projects",
                headers=org_headers(token_a, org_b_id),
            )

            check(
                "Token A, organization B -> 404",
                foreign_org_response.status_code == 404,
                response=foreign_org_response,
            )

            nonexistent_org_response = await client.get(
                "/projects",
                headers=org_headers(token_a, INT4_MAX),
            )
            check(
                "Nonexistent organization -> 404",
                nonexistent_org_response.status_code == 404,
                response=nonexistent_org_response,
            )

            check(
                "Foreign and nonexistent organizations return identical 404 bodies",
                (
                    foreign_org_response.status_code == 404
                    and nonexistent_org_response.status_code == 404
                    and json_body(foreign_org_response) == json_body(nonexistent_org_response)
                ),
                response=foreign_org_response,
            )

            for invalid_org_id in ("abc", "0"):
                response = await client.get(
                    "/projects",
                    headers=org_headers(token_a, invalid_org_id),
                )
                check(
                    f"X-Organization-ID={invalid_org_id!r} -> 422",
                    response.status_code == 422,
                    response=response,
                )

            response = await client.get(
                "/projects",
                headers={"Authorization": f"Bearer {token_a}"},
            )
            check(
                "GET /projects without X-Organization-ID -> 422",
                response.status_code == 422,
                response=response,
            )

            # 12. Проверка составного внешнего ключа в БД.

            composite_fk_passed, composite_fk_details = await check_composite_fk(
                invalid_organization_id=int(org_a_id),
                invalid_project_id=int(project_b_id),
                valid_organization_id=int(org_a_id),
                valid_project_id=int(project_a_id),
            )

            check(
                "Composite FK rejects mismatched organization/project pair "
                "and accepts matching pair",
                composite_fk_passed,
                details=composite_fk_details,
            )

            # 13. Удаление чужого проекта запрещено.

            response = await client.delete(
                f"/projects/{project_b_id}",
                headers=headers_a,
            )

            check(
                "OWNER A DELETE project B -> 404",
                response.status_code == 404,
                response=response,
            )

            project_b_after_delete = await client.get(
                f"/projects/{project_b_id}",
                headers=headers_b,
            )

            check(
                "Project B still exists after cross-org DELETE attempt",
                project_b_after_delete.status_code == 200,
                response=project_b_after_delete,
            )

            # 14. Удаление проекта и проверка каскада.

            response = await client.delete(
                f"/projects/{project_a_id}",
                headers=headers_a,
            )

            owner_delete_succeeded = check(
                "OWNER DELETE /projects/{id} -> 204",
                response.status_code == 204,
                response=response,
            )

            if owner_delete_succeeded:
                project_after_delete = await client.get(
                    f"/projects/{project_a_id}",
                    headers=headers_a,
                )
                check(
                    "Deleted project GET -> 404",
                    project_after_delete.status_code == 404,
                    response=project_after_delete,
                )

                task_after_delete = await client.get(
                    f"/tasks/{task_a_id}",
                    headers=headers_a,
                )
                check(
                    "Task removed by ON DELETE CASCADE -> 404",
                    task_after_delete.status_code == 404,
                    response=task_after_delete,
                )
            else:
                check(
                    "Deleted project GET -> 404",
                    False,
                    details=("Skipped because owner deletion did not return 204"),
                )
                check(
                    "Task removed by ON DELETE CASCADE -> 404",
                    False,
                    details=("Skipped because owner deletion did not return 204"),
                )

            # 15. Проверка границ ResourceID.

            response = await client.get(
                "/tasks/99999999999",
                headers=headers_a,
            )
            check(
                "GET /tasks/99999999999 -> 422",
                response.status_code == 422,
                response=response,
            )

            # 16. GET /projects без токена.

            response = await client.get("/projects")
            check(
                "GET /projects without token -> 401",
                response.status_code == 401,
                response=response,
            )

            # 17. Удаление membership немедленно отзывает доступ.

            await remove_membership(user_b_id, org_b_id)

            response = await client.get(
                "/projects",
                headers=headers_b,
            )
            check(
                "Removed membership invalidates access immediately -> 404",
                response.status_code == 404,
                response=response,
            )

    finally:
        await test_engine.dispose()

    passed = sum(1 for _, ok in results if ok)
    failed = len(results) - passed

    print()
    print(f"Acceptance checks: {passed} passed, {failed} failed.")

    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except (httpx.HTTPError, RuntimeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)
