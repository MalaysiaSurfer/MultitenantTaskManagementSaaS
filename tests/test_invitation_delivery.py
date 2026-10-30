import logging
import smtplib
from datetime import UTC, datetime, timedelta

import pytest
from app.core.config import settings
from app.db.base import Invitation, Membership, MembershipRole
from sqlalchemy import update

from tests.test_invitations import invitation_token


class FakeSMTP:
    sent: list = []
    instances: list = []

    def __init__(self, host, port, timeout):
        self.host = host
        self.port = port
        self.started_tls = False
        self.credentials = None
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        self.started_tls = True

    def login(self, username, password):
        self.credentials = (username, password)

    def send_message(self, message):
        FakeSMTP.sent.append(message)


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.sent = []
    FakeSMTP.instances = []
    monkeypatch.setattr("app.services.mailer.smtplib.SMTP", FakeSMTP)
    monkeypatch.setattr(settings, "smtp_host", "smtp.test")
    monkeypatch.setattr(settings, "smtp_port", 2525)
    monkeypatch.setattr(settings, "smtp_username", "mailer")
    monkeypatch.setattr(settings, "smtp_password", "mailer-password")
    monkeypatch.setattr(settings, "mail_from", "noreply@example.com")
    monkeypatch.setattr(
        settings, "invitation_accept_url", "https://app.example.com/invite?token={token}"
    )
    return FakeSMTP


async def test_invitation_is_emailed_with_accept_link_and_token_is_not_logged(
    client, user_factory, smtp, caplog
):
    owner = await user_factory("owner@example.com", "Mail Org")
    caplog.set_level(logging.INFO, logger="app.services.invitation")
    response = await client.post(
        "/organizations/invite",
        headers=owner.headers,
        json={"email": "invitee@example.com", "role": "member"},
    )
    assert response.status_code == 202

    assert len(smtp.sent) == 1
    message = smtp.sent[0]
    assert message["To"] == "invitee@example.com"
    assert message["From"] == "noreply@example.com"
    assert "Mail Org" in message["Subject"]
    body = message.get_content()
    assert "https://app.example.com/invite?token=" in body
    token = body.split("token=", 1)[1].split()[0]
    connection = smtp.instances[0]
    assert (connection.host, connection.port) == ("smtp.test", 2525)
    assert connection.started_tls
    assert connection.credentials == ("mailer", "mailer-password")
    assert all(token not in record.getMessage() for record in caplog.records)

    invitee = await user_factory("invitee@example.com", "Invitee home")
    accepted = await client.post(
        "/invitations/accept",
        headers={"Authorization": f"Bearer {invitee.token}"},
        json={"token": token},
    )
    assert accepted.status_code == 200


async def test_existing_member_is_not_emailed(client, user_factory, smtp):
    owner = await user_factory("owner@example.com", "Mail Org")
    response = await client.post(
        "/organizations/invite",
        headers=owner.headers,
        json={"email": "owner@example.com", "role": "member"},
    )
    assert response.status_code == 202
    assert smtp.sent == []


async def test_smtp_failure_keeps_uniform_response_and_logs_error(
    client, user_factory, smtp, caplog, monkeypatch
):
    def broken_send(self, message):
        raise smtplib.SMTPServerDisconnected("connection lost")

    monkeypatch.setattr(FakeSMTP, "send_message", broken_send)
    owner = await user_factory("owner@example.com", "Mail Org")
    caplog.set_level(logging.INFO, logger="app.services.invitation")
    response = await client.post(
        "/organizations/invite",
        headers=owner.headers,
        json={"email": "invitee@example.com", "role": "member"},
    )
    assert response.status_code == 202
    assert response.json() == {"detail": "invitation processed"}
    assert any(
        record.levelno == logging.ERROR and "Could not deliver invitation" in record.getMessage()
        for record in caplog.records
    )


async def test_production_without_smtp_does_not_log_token(
    client, user_factory, caplog, monkeypatch
):
    owner = await user_factory("owner@example.com", "Prod Org")
    monkeypatch.setattr(settings, "environment", "prod")
    caplog.set_level(logging.INFO, logger="app.services.invitation")
    response = await client.post(
        "/organizations/invite",
        headers=owner.headers,
        json={"email": "invitee@example.com", "role": "member"},
    )
    assert response.status_code == 202
    messages = [record.getMessage() for record in caplog.records]
    assert not any("token=" in message for message in messages)
    assert any("SMTP is not configured" in message for message in messages)


async def test_admin_lists_only_pending_invitations_with_pagination(
    client, user_factory, caplog, session_factory
):
    owner = await user_factory("owner@example.com", "List Org")
    caplog.set_level(logging.INFO, logger="app.services.invitation")
    for email in ("a@example.com", "b@example.com", "c@example.com", "d@example.com"):
        response = await client.post(
            "/organizations/invite", headers=owner.headers, json={"email": email, "role": "member"}
        )
        assert response.status_code == 202

    accepted_user = await user_factory("c@example.com", "C home")
    accepted = await client.post(
        "/invitations/accept",
        headers={"Authorization": f"Bearer {accepted_user.token}"},
        json={"token": invitation_token(caplog, "c@example.com")},
    )
    assert accepted.status_code == 200
    async with session_factory() as db:
        await db.execute(
            update(Invitation)
            .where(Invitation.email == "d@example.com")
            .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )
        await db.commit()

    first = await client.get("/organizations/invitations?limit=1", headers=owner.headers)
    assert first.status_code == 200
    first_page = first.json()
    assert [item["email"] for item in first_page["items"]] == ["a@example.com"]
    assert first_page["items"][0]["role"] == "member"
    assert first_page["items"][0]["invited_by_id"] == owner.user_id
    assert "token" not in first_page["items"][0]
    assert "token_hash" not in first_page["items"][0]

    second = await client.get(
        f"/organizations/invitations?limit=1&cursor={first_page['next_cursor']}",
        headers=owner.headers,
    )
    assert second.json() == {"items": second.json()["items"], "next_cursor": None}
    assert [item["email"] for item in second.json()["items"]] == ["b@example.com"]


async def test_member_cannot_list_invitations(client, user_factory, session_factory):
    owner = await user_factory("owner@example.com", "List Org")
    member = await user_factory("member@example.com", "Member home")
    async with session_factory() as db:
        db.add(
            Membership(
                user_id=member.user_id, organization_id=owner.org_id, role=MembershipRole.MEMBER
            )
        )
        await db.commit()
    response = await client.get(
        "/organizations/invitations",
        headers={"Authorization": f"Bearer {member.token}", "X-Organization-ID": str(owner.org_id)},
    )
    assert response.status_code == 403
