import hashlib
import logging
from datetime import UTC, datetime, timedelta

import pytest
from app.db.base import Invitation
from sqlalchemy import select

from tests.helpers import auth_headers


def invitation_token(caplog, email):
    for record in reversed(caplog.records):
        if "Invitation for " + email in record.getMessage():
            return record.getMessage().split("token=", 1)[1]
    raise AssertionError("invitation token was not logged")


@pytest.mark.asyncio
async def test_invite_privacy_and_token_hash_storage(client, user_factory, caplog, session_factory):
    owner = await user_factory("owner@example.com", "Invite Org")
    await user_factory("other@example.com", "Other Org")
    caplog.set_level(logging.INFO, logger="app.services.invitation")
    responses = []
    for email in ("owner@example.com", "other@example.com", "unknown@example.com"):
        responses.append(
            await client.post(
                "/organizations/invite",
                headers=owner.headers,
                json={"email": email, "role": "member"},
            )
        )
    assert [response.status_code for response in responses] == [202, 202, 202]
    assert responses[0].json() == responses[1].json() == responses[2].json()
    token = invitation_token(caplog, "unknown@example.com")
    async with session_factory() as db:
        row = await db.scalar(select(Invitation).where(Invitation.email == "unknown@example.com"))
        assert row.token_hash == hashlib.sha256(token.encode()).hexdigest()
        assert row.token_hash != token


@pytest.mark.asyncio
async def test_wrong_user_cannot_accept_pending_invitation_then_matching_user_can(
    client, user_factory, caplog, session_factory
):
    owner = await user_factory("owner@example.com", "Invite Org")
    caplog.set_level(logging.INFO, logger="app.services.invitation")
    await client.post(
        "/organizations/invite",
        headers=owner.headers,
        json={"email": "join@example.com", "role": "member"},
    )
    token = invitation_token(caplog, "join@example.com")
    wrong = await user_factory("wrong@example.com", "Wrong")
    rejected = await client.post(
        "/invitations/accept", headers=auth_headers(wrong.token), json={"token": token}
    )
    assert rejected.status_code == 404
    async with session_factory() as db:
        pending = await db.scalar(select(Invitation).where(Invitation.email == "join@example.com"))
        assert pending.accepted_at is None

    joined = await user_factory("join@example.com", "Join Home")
    accepted = await client.post(
        "/invitations/accept", headers=auth_headers(joined.token), json={"token": token}
    )
    assert accepted.status_code == 200
    organizations = await client.get("/organizations", headers=auth_headers(joined.token))
    owner_membership = next(org for org in organizations.json() if org["id"] == owner.org_id)
    assert owner_membership["role"] == "member"
    assert (
        await client.post(
            "/invitations/accept", headers=auth_headers(joined.token), json={"token": token}
        )
    ).status_code == 404


@pytest.mark.asyncio
async def test_reinviting_same_email_invalidates_old_token(client, user_factory, caplog):
    owner = await user_factory("owner@example.com", "Invite Org")
    caplog.set_level(logging.INFO, logger="app.services.invitation")
    payload = {"email": "target@example.com", "role": "member"}
    await client.post("/organizations/invite", headers=owner.headers, json=payload)
    old = invitation_token(caplog, payload["email"])
    await client.post("/organizations/invite", headers=owner.headers, json=payload)
    new = invitation_token(caplog, payload["email"])
    assert old != new
    target = await user_factory(payload["email"], "Target")
    old_response = await client.post(
        "/invitations/accept", headers=auth_headers(target.token), json={"token": old}
    )
    assert old_response.status_code == 404


@pytest.mark.asyncio
async def test_replacement_invitation_token_can_be_accepted(client, user_factory, caplog):
    owner = await user_factory("owner@example.com", "Invite Org")
    caplog.set_level(logging.INFO, logger="app.services.invitation")
    payload = {"email": "target@example.com", "role": "member"}
    await client.post("/organizations/invite", headers=owner.headers, json=payload)
    await client.post("/organizations/invite", headers=owner.headers, json=payload)
    new = invitation_token(caplog, payload["email"])
    target = await user_factory(payload["email"], "Target")
    response = await client.post(
        "/invitations/accept", headers=auth_headers(target.token), json={"token": new}
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_expired_invitation_token_cannot_be_accepted(
    client, user_factory, caplog, session_factory
):
    owner = await user_factory("owner@example.com", "Invite Org")
    caplog.set_level(logging.INFO, logger="app.services.invitation")
    await client.post(
        "/organizations/invite",
        headers=owner.headers,
        json={"email": "expired@example.com", "role": "member"},
    )
    token = invitation_token(caplog, "expired@example.com")
    async with session_factory() as db:
        row = await db.scalar(select(Invitation).where(Invitation.email == "expired@example.com"))
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await db.commit()
    target = await user_factory("expired@example.com", "Expired")
    response = await client.post(
        "/invitations/accept", headers=auth_headers(target.token), json={"token": token}
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_already_member_conflict_keeps_invitation_pending(
    client, user_factory, caplog, session_factory
):
    owner = await user_factory("owner@example.com", "Invite Org")
    caplog.set_level(logging.INFO, logger="app.services.invitation")
    email = "late-member@example.com"
    await client.post(
        "/organizations/invite",
        headers=owner.headers,
        json={"email": email, "role": "member"},
    )
    token = invitation_token(caplog, email)
    from app.db.base import Membership, MembershipRole

    member = await user_factory(email, "Home")
    async with session_factory() as db:
        db.add(
            Membership(
                user_id=member.user_id, organization_id=owner.org_id, role=MembershipRole.MEMBER
            )
        )
        await db.commit()
    response = await client.post(
        "/invitations/accept", headers=auth_headers(member.token), json={"token": token}
    )
    assert response.status_code == 409
    async with session_factory() as db:
        invitation = await db.scalar(
            select(Invitation).where(
                Invitation.token_hash == hashlib.sha256(token.encode()).hexdigest()
            )
        )
        assert invitation.accepted_at is None


@pytest.mark.asyncio
async def test_owner_can_invite_and_grant_admin_role(client, user_factory, caplog):
    owner = await user_factory("owner@example.com", "Invite Org")
    caplog.set_level(logging.INFO, logger="app.services.invitation")
    email = "new-admin@example.com"
    response = await client.post(
        "/organizations/invite",
        headers=owner.headers,
        json={"email": email, "role": "admin"},
    )
    assert response.status_code == 202
    token = invitation_token(caplog, email)
    new_admin = await user_factory(email, "Admin Home")
    accepted = await client.post(
        "/invitations/accept", headers=auth_headers(new_admin.token), json={"token": token}
    )
    assert accepted.status_code == 200
    organizations = await client.get("/organizations", headers=auth_headers(new_admin.token))
    membership = next(org for org in organizations.json() if org["id"] == owner.org_id)
    assert membership["role"] == "admin"


@pytest.mark.asyncio
async def test_invitation_cannot_be_accepted_after_inviter_is_demoted(
    client, user_factory, caplog, session_factory
):
    from app.db.base import Membership, MembershipRole

    owner = await user_factory("owner-retain@example.com", "Retained owner")
    inviter = await user_factory("inviter-retain@example.com", "Inviter home")
    another_owner = await user_factory("another-owner@example.com", "Owner home")
    async with session_factory() as db:
        db.add(
            Membership(
                user_id=inviter.user_id,
                organization_id=owner.org_id,
                role=MembershipRole.ADMIN,
            )
        )
        db.add(
            Membership(
                user_id=another_owner.user_id,
                organization_id=owner.org_id,
                role=MembershipRole.OWNER,
            )
        )
        await db.commit()
    caplog.set_level(logging.INFO, logger="app.services.invitation")
    sent = await client.post(
        "/organizations/invite",
        headers=auth_headers(inviter.token, owner.org_id),
        json={"email": "invitee-retain@example.com", "role": "member"},
    )
    assert sent.status_code == 202
    token = invitation_token(caplog, "invitee-retain@example.com")
    async with session_factory() as db:
        membership = await db.scalar(
            select(Membership).where(
                Membership.user_id == inviter.user_id,
                Membership.organization_id == owner.org_id,
            )
        )
        membership.role = MembershipRole.MEMBER
        await db.commit()
    invitee = await user_factory("invitee-retain@example.com", "Invitee home")
    response = await client.post(
        "/invitations/accept", headers=auth_headers(invitee.token), json={"token": token}
    )
    assert response.status_code == 404
