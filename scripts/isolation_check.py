import asyncio
import hashlib
import os
import re
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

import httpx
from app.core.config import settings
from app.core.constants import INT4_MAX
from app.db.base import Invitation, Membership, MembershipRole, Task, User
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
EMAIL_INVITATION_RECIPIENT = f"invitation-recipient-{RUN_ID}@example.com"
EMAIL_INVITATION_OTHER = f"invitation-other-{RUN_ID}@example.com"
EMAIL_INVITATION_EXPIRED = f"invitation-expired-{RUN_ID}@example.com"
EMAIL_INVITATION_UNKNOWN = f"invitation-unknown-{RUN_ID}@example.com"

ORG_NAME_A = f"Isolation Org A {RUN_ID}"
ORG_NAME_B = f"Isolation Org B {RUN_ID}"
ORG_NAME_ADMIN = f"Isolation Admin Org {RUN_ID}"
ORG_NAME_MEMBER = f"Isolation Member Org {RUN_ID}"

PROJECT_NAME_A = f"Project A {RUN_ID}"
PROJECT_NAME_B = f"Project B {RUN_ID}"
PROJECT_NAME_RENAMED = f"Project A Renamed {RUN_ID}"

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
    """Ищет ID сущности по её имени."""

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


def find_task(data: Any, title: str) -> dict[str, Any] | None:
    """Возвращает объект задачи с указанным названием из JSON-ответа."""

    if isinstance(data, list):
        for item in data:
            result = find_task(item, title)
            if result is not None:
                return result

    elif isinstance(data, dict):
        if data.get("title") == title:
            return data

        for value in data.values():
            if isinstance(value, (dict, list)):
                result = find_task(value, title)
                if result is not None:
                    return result

    return None


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


async def send_invitation(
    client: httpx.AsyncClient,
    inviter_token: str,
    organization_id: int | str,
    email: str,
    role: str,
) -> httpx.Response:
    """Отправляет приглашение через API; генерация токена происходит в API-процессе."""

    return await client.post(
        "/organizations/invite",
        headers=org_headers(inviter_token, organization_id),
        json={"email": email, "role": role},
    )


def get_logged_invitation_token(
    email: str,
    organization_id: int | str,
) -> str | None:
    """Извлекает токен из лога API, поскольку токен генерируется другим процессом."""

    try:
        completed = subprocess.run(
            ["docker", "compose", "logs", "--since", "10m", "--no-color", "api"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            cwd=ROOT_DIR,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    logs = f"{completed.stdout}\n{completed.stderr}"
    pattern = re.compile(
        rf"Invitation for {re.escape(email)} to organization "
        rf"{re.escape(str(organization_id))}, token=([^\s]+)"
    )
    matches = pattern.findall(logs)
    return matches[-1] if matches else None


async def get_invitation(
    organization_id: int | str,
    email: str,
) -> Invitation | None:
    """Возвращает ожидающее приглашение по организации и email."""

    async with async_session() as session:
        result = await session.execute(
            select(Invitation).where(
                Invitation.organization_id == organization_id,
                Invitation.email == email,
                Invitation.accepted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()


async def expire_invitation(
    organization_id: int | str,
    email: str,
) -> bool:
    """Просрочивает ожидающее приглашение непосредственно в БД."""

    async with async_session() as session:
        result = await session.execute(
            select(Invitation).where(
                Invitation.organization_id == organization_id,
                Invitation.email == email,
                Invitation.accepted_at.is_(None),
            )
        )
        invitation = result.scalar_one_or_none()
        if invitation is None:
            return False

        invitation.expires_at = datetime.now(UTC) - timedelta(days=1)
        await session.commit()
        return True


async def get_user_id_by_email(email: str) -> int | None:
    async with async_session() as session:
        return await session.scalar(select(User.id).where(User.email == email))


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
            await register(
                client,
                EMAIL_INVITATION_RECIPIENT,
                f"Invitation Recipient Org {RUN_ID}",
            )
            await register(
                client,
                EMAIL_INVITATION_OTHER,
                f"Invitation Other Org {RUN_ID}",
            )
            await register(
                client,
                EMAIL_INVITATION_EXPIRED,
                f"Invitation Expired Org {RUN_ID}",
            )

            token_a = await login(client, EMAIL_A)
            token_b = await login(client, EMAIL_B)
            token_admin = await login(client, EMAIL_ADMIN)
            token_member = await login(client, EMAIL_MEMBER)
            token_invitation_recipient = await login(client, EMAIL_INVITATION_RECIPIENT)
            token_invitation_other = await login(client, EMAIL_INVITATION_OTHER)
            token_invitation_expired = await login(client, EMAIL_INVITATION_EXPIRED)

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

            # 1.1. Проверка прав и жизненного цикла приглашений.

            # ADMIN может приглашать MEMBER.
            response = await send_invitation(
                client,
                token_admin,
                org_a_id,
                EMAIL_INVITATION_RECIPIENT,
                "member",
            )
            check(
                "ADMIN invites MEMBER -> 202",
                response.status_code == 202,
                response=response,
            )

            # ADMIN не может приглашать ADMIN.
            response = await send_invitation(
                client,
                token_admin,
                org_a_id,
                EMAIL_INVITATION_OTHER,
                "admin",
            )
            check(
                "ADMIN invites ADMIN -> 403",
                response.status_code == 403,
                response=response,
            )

            # OWNER может приглашать ADMIN.
            response = await send_invitation(
                client,
                token_a,
                org_a_id,
                EMAIL_INVITATION_OTHER,
                "admin",
            )
            check(
                "OWNER invites ADMIN -> 202",
                response.status_code == 202,
                response=response,
            )
            invitation_other_token = get_logged_invitation_token(
                EMAIL_INVITATION_OTHER,
                org_a_id,
            )
            check(
                "Read admin invitation token from API log",
                invitation_other_token is not None,
            )
            if invitation_other_token is None:
                raise RuntimeError("Could not read admin invitation token from API logs")

            # role=owner запрещён схемой запроса.
            response = await client.post(
                "/organizations/invite",
                headers=headers_a,
                json={
                    "email": f"invitation-owner-{RUN_ID}@example.com",
                    "role": "owner",
                },
            )
            check(
                "role=owner -> 422",
                response.status_code == 422,
                response=response,
            )

            # MEMBER не может приглашать пользователей.
            response = await client.post(
                "/organizations/invite",
                headers=headers_member,
                json={
                    "email": f"invitation-forbidden-{RUN_ID}@example.com",
                    "role": "member",
                },
            )
            check(
                "MEMBER invites MEMBER -> 403",
                response.status_code == 403,
                response=response,
            )

            # Ответы для существующего пользователя, неизвестного email и
            # уже состоящего в организации участника не раскрывают состояние.
            privacy_cases = (
                (EMAIL_INVITATION_RECIPIENT, "existing user not in organization"),
                (EMAIL_INVITATION_UNKNOWN, "unknown email"),
                (EMAIL_MEMBER, "existing organization member"),
            )
            privacy_responses: list[tuple[str, httpx.Response]] = []
            for index, (email, label) in enumerate(privacy_cases):
                response = await send_invitation(
                    client,
                    token_a,
                    org_a_id,
                    email,
                    "member",
                )
                privacy_responses.append((label, response))
                check(
                    f"Invitation privacy response: {label} -> 202",
                    response.status_code == 202,
                    response=response,
                )

            reference_status = privacy_responses[0][1].status_code
            reference_body = json_body(privacy_responses[0][1])
            check(
                "Invitation responses do not reveal whether email exists or is already a member",
                all(
                    response.status_code == reference_status
                    and json_body(response) == reference_body
                    for _, response in privacy_responses
                ),
                response=privacy_responses[0][1],
                details="Compared status and JSON body for all three email cases.",
            )

            # Повторно создаём приглашение с известным токеном после privacy-теста,
            # который мог обновить хэш ожидающего приглашения.
            response = await send_invitation(
                client,
                token_a,
                org_a_id,
                EMAIL_INVITATION_RECIPIENT,
                "member",
            )
            check(
                "Create invitation for acceptance tests -> 202",
                response.status_code == 202,
                response=response,
            )
            invitation_recipient_token = get_logged_invitation_token(
                EMAIL_INVITATION_RECIPIENT,
                org_a_id,
            )
            check(
                "Read recipient invitation token from API log",
                invitation_recipient_token is not None,
                details=(
                    "Ensure API INFO logs include the invitation log entry and "
                    "docker compose logs can access the api service."
                ) if invitation_recipient_token is None else "",
            )
            if invitation_recipient_token is None:
                raise RuntimeError("Could not read recipient invitation token from API logs")

            stored_invitation = await get_invitation(
                org_a_id,
                EMAIL_INVITATION_RECIPIENT,
            )
            check(
                "Invitation stores only the token hash",
                stored_invitation is not None
                and stored_invitation.token_hash
                == hashlib.sha256(
                    invitation_recipient_token.encode("utf-8")
                ).hexdigest(),
            )

            # Пользователь организации B получает 404 на токен приглашения в A;
            # приглашение остаётся активным для настоящего получателя.
            response = await client.post(
                "/invitations/accept",
                headers={"Authorization": f"Bearer {token_b}"},
                json={"token": invitation_recipient_token},
            )
            check(
                "User B cannot accept invitation addressed to another email -> 404",
                response.status_code == 404,
                response=response,
            )

            pending_after_wrong_user = await get_invitation(
                org_a_id,
                EMAIL_INVITATION_RECIPIENT,
            )
            check(
                "Wrong-user attempt leaves invitation pending",
                pending_after_wrong_user is not None
                and pending_after_wrong_user.accepted_at is None,
            )

            # Настоящий получатель принимает приглашение.
            response = await client.post(
                "/invitations/accept",
                headers={"Authorization": f"Bearer {token_invitation_recipient}"},
                json={"token": invitation_recipient_token},
            )
            accepted_data = json_body(response)
            check(
                "Correct user accepts invitation -> 200",
                response.status_code == 200,
                response=response,
            )
            check(
                "Accepted invitation returns organization A and MEMBER role",
                isinstance(accepted_data, dict)
                and str(accepted_data.get("organization_id")) == str(org_a_id)
                and accepted_data.get("role") == "member",
                response=response,
            )

            recipient_orgs_response = await client.get(
                "/organizations",
                headers={"Authorization": f"Bearer {token_invitation_recipient}"},
            )
            check(
                "Accepted organization appears in GET /organizations",
                recipient_orgs_response.status_code == 200
                and extract_named_id(
                    json_body(recipient_orgs_response),
                    ORG_NAME_A,
                ) is not None,
                response=recipient_orgs_response,
            )

            # Повторное принятие одноразового токена запрещено.
            response = await client.post(
                "/invitations/accept",
                headers={"Authorization": f"Bearer {token_invitation_recipient}"},
                json={"token": invitation_recipient_token},
            )
            check(
                "Reusing accepted invitation token -> 404",
                response.status_code == 404,
                response=response,
            )

            # Приглашение A с email другого пользователя не может добавить его в B.
            response = await client.post(
                "/invitations/accept",
                headers={"Authorization": f"Bearer {token_invitation_other}"},
                json={"token": invitation_other_token},
            )
            cross_org_accept_data = json_body(response)
            check(
                "Token issued for organization A accepts only into A -> 200",
                response.status_code == 200
                and isinstance(cross_org_accept_data, dict)
                and str(cross_org_accept_data.get("organization_id")) == str(org_a_id),
                response=response,
            )
            other_orgs_response = await client.get(
                "/organizations",
                headers={"Authorization": f"Bearer {token_invitation_other}"},
            )
            check(
                "Invitation token for A does not add user to organization B",
                other_orgs_response.status_code == 200
                and extract_named_id(json_body(other_orgs_response), ORG_NAME_B) is None,
                response=other_orgs_response,
            )

            # Просроченный токен должен возвращать 404.
            response = await send_invitation(
                client,
                token_a,
                org_a_id,
                EMAIL_INVITATION_EXPIRED,
                "member",
            )
            check(
                "Create invitation for expiration test -> 202",
                response.status_code == 202,
                response=response,
            )
            invitation_expired_token = get_logged_invitation_token(
                EMAIL_INVITATION_EXPIRED,
                org_a_id,
            )
            check(
                "Read expiration-test token from API log",
                invitation_expired_token is not None,
            )
            if invitation_expired_token is None:
                raise RuntimeError("Could not read expiration-test token from API logs")
            expired_in_db = await expire_invitation(org_a_id, EMAIL_INVITATION_EXPIRED)
            check("Expiration test invitation exists in DB", expired_in_db)
            response = await client.post(
                "/invitations/accept",
                headers={"Authorization": f"Bearer {token_invitation_expired}"},
                json={"token": invitation_expired_token},
            )
            check(
                "Accept expired invitation -> 404",
                response.status_code == 404,
                response=response,
            )

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

            # 4. Переименование проекта и проверка уникальности имени.

            response = await client.patch(
                f"/projects/{project_a_id}",
                headers=headers_a,
                json={"name": PROJECT_NAME_RENAMED},
            )
            rename_project_succeeded = check(
                "PATCH project A with a new name -> 200",
                response.status_code == 200,
                response=response,
            )

            if rename_project_succeeded:
                response = await client.get(f"/projects/{project_a_id}", headers=headers_a)
                data = json_body(response)
                check(
                    "GET project A after rename returns the new name",
                    (
                        response.status_code == 200
                        and isinstance(data, dict)
                        and data.get("name") == PROJECT_NAME_RENAMED
                    ),
                    response=response,
                )
            else:
                check(
                    "GET project A after rename returns the new name",
                    False,
                    details="Skipped because project rename did not return 200",
                )

            other_name = f"Rename Target {RUN_ID}"
            other_project_response = await create_project(client, token_a, org_a_id, other_name)

            if check(
                "Create second project for rename-conflict check",
                other_project_response.status_code == 201,
                response=other_project_response,
            ):
                response = await client.patch(
                    f"/projects/{project_a_id}",
                    headers=headers_a,
                    json={"name": other_name},
                )
                check(
                    "PATCH project to existing name in same organization -> 409",
                    response.status_code == 409,
                    response=response,
                )

            # 5. Создание задач.

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

            # MEMBER не может создавать, изменять или удалять задачи.
            response = await create_task(
                client,
                token_member,
                org_a_id,
                project_a_id,
                f"Member forbidden task {RUN_ID}",
            )
            check(
                "MEMBER POST /tasks -> 403",
                response.status_code == 403,
                response=response,
            )

            response = await client.patch(
                f"/tasks/{task_a_id}",
                headers=headers_member,
                json={"title": f"Member forbidden update {RUN_ID}"},
            )
            check(
                "MEMBER PATCH /tasks/{id} -> 403",
                response.status_code == 403,
                response=response,
            )

            response = await client.delete(
                f"/tasks/{task_a_id}",
                headers=headers_member,
            )
            check(
                "MEMBER DELETE /tasks/{id} -> 403",
                response.status_code == 403,
                response=response,
            )

            # 6. Позитивная проверка поиска задач.

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

            # 7. Проверка PATCH-валидации задачи.

            for payload, label in (
                ({}, "empty body"),
                ({"title": None}, "title=null"),
                ({"project_id": 1}, "project_id is not updatable"),
            ):
                response = await client.patch(
                    f"/tasks/{task_a_id}",
                    headers=headers_a,
                    json=payload,
                )
                check(
                    f"PATCH task with {label} -> 422",
                    response.status_code == 422,
                    response=response,
                )

            # 8. PATCH с исполнителем из другой организации должен отклоняться
            # и не должен менять задачу.

            task_before_cross_assignee = await client.get(
                f"/tasks/{task_a_id}",
                headers=headers_a,
            )
            before_data = json_body(task_before_cross_assignee)

            response = await client.patch(
                f"/tasks/{task_a_id}",
                headers=headers_a,
                json={"assignee_id": user_b_id},
            )
            check(
                "PATCH task with cross-org assignee -> 404",
                response.status_code == 404,
                response=response,
            )

            task_after_cross_assignee = await client.get(
                f"/tasks/{task_a_id}",
                headers=headers_a,
            )
            after_data = json_body(task_after_cross_assignee)

            check(
                "Rejected cross-org assignee PATCH leaves task unchanged",
                (
                    task_before_cross_assignee.status_code == 200
                    and task_after_cross_assignee.status_code == 200
                    and before_data == after_data
                ),
                response=task_after_cross_assignee,
                details=(
                    f"Before: {before_data}; after: {after_data}"
                    if before_data != after_data
                    else ""
                ),
            )

            # 9. PATCH с assignee_id=null снимает исполнителя.
            # Сначала назначаем участника той же организации.

            response = await client.patch(
                f"/tasks/{task_a_id}",
                headers=headers_a,
                json={"assignee_id": member_id},
            )
            assigned_member = check(
                "PATCH task to assign an in-org member -> 200",
                response.status_code == 200
                and isinstance(json_body(response), dict)
                and json_body(response).get("assignee_id") == member_id,
                response=response,
            )

            if assigned_member:
                response = await client.patch(
                    f"/tasks/{task_a_id}",
                    headers=headers_a,
                    json={"assignee_id": None},
                )
                check(
                    "PATCH task with assignee_id=null -> 200 and assignee cleared",
                    (
                        response.status_code == 200
                        and isinstance(json_body(response), dict)
                        and json_body(response).get("assignee_id") is None
                    ),
                    response=response,
                )

                response = await client.get(
                    f"/tasks/{task_a_id}",
                    headers=headers_a,
                )
                check(
                    "GET task confirms assignee_id is null",
                    (
                        response.status_code == 200
                        and isinstance(json_body(response), dict)
                        and json_body(response).get("assignee_id") is None
                    ),
                    response=response,
                )
            else:
                check(
                    "PATCH task with assignee_id=null -> 200 and assignee cleared",
                    False,
                    details="Skipped because assigning an in-org member failed",
                )
                check(
                    "GET task confirms assignee_id is null",
                    False,
                    details="Skipped because assigning an in-org member failed",
                )

            # 10. Изоляция чтения.

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

            # 11. Попытка создать задачу в чужом проекте.

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

            # 12. Попытка назначить исполнителя из другой организации при создании.

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

                # 13. Дубликат имени проекта при создании.

                response = await create_project(
                    client,
                    token_a,
                    org_a_id,
                    PROJECT_NAME_RENAMED,
                )
                check(
                    "Duplicate project name -> 409",
                    response.status_code == 409,
                    response=response,
                )

            # 14. Проверка запретов ролей.

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

            # 15. Проверка X-Organization-ID.

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

            # 16. Проверка составного внешнего ключа в БД.

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

            # 17. Удаление чужого проекта запрещено.

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

            # 18. Удаление чужой задачи запрещено.

            response = await client.delete(
                f"/tasks/{task_b_id}",
                headers=headers_a,
            )
            check(
                "OWNER A DELETE task B -> 404",
                response.status_code == 404,
                response=response,
            )

            task_b_after_delete = await client.get(
                f"/tasks/{task_b_id}",
                headers=headers_b,
            )
            check(
                "Task B still exists after cross-org DELETE attempt",
                (
                    task_b_after_delete.status_code == 200
                    and contains_task(json_body(task_b_after_delete), task_b_title)
                ),
                response=task_b_after_delete,
            )

            # 19. Удаление проекта и проверка каскада.

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
                    details="Skipped because owner deletion did not return 204",
                )
                check(
                    "Task removed by ON DELETE CASCADE -> 404",
                    False,
                    details="Skipped because owner deletion did not return 204",
                )

            # 20. Проверка границ ResourceID.

            response = await client.get(
                "/tasks/99999999999",
                headers=headers_a,
            )
            check(
                "GET /tasks/99999999999 -> 422",
                response.status_code == 422,
                response=response,
            )

            # 21. GET /projects без токена.

            response = await client.get("/projects")
            check(
                "GET /projects without token -> 401",
                response.status_code == 401,
                response=response,
            )

            # 22. Удаление membership немедленно отзывает доступ.

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
