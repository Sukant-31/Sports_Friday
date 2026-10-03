"""Duplicate signup handling, including real local PostgreSQL insertion races."""

import asyncio
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import asyncpg
import httpx
import pytest
from fastapi import HTTPException

from app import db
from app.config import settings
from app.main import create_app
from app.repositories import users as users_repo
from app.security import decode_token, verify_password
from app.services import auth_service


async def test_ordinary_duplicate_preserves_409_before_hashing(monkeypatch):
    lookup = AsyncMock(return_value={"id": uuid4(), "email": "existing@example.com"})
    create = AsyncMock()
    hashing = Mock()
    monkeypatch.setattr(users_repo, "find_user_by_email", lookup)
    monkeypatch.setattr(users_repo, "create_user", create)
    monkeypatch.setattr(auth_service, "hash_password", hashing)
    with pytest.raises(HTTPException) as error:
        await auth_service.signup("existing@EXAMPLE.COM", "valid-password")
    assert error.value.status_code == 409
    assert error.value.detail == "Email already registered"
    lookup.assert_awaited_once_with("existing@example.com")
    hashing.assert_not_called()
    create.assert_not_awaited()


async def test_email_unique_violation_returns_existing_conflict(monkeypatch):
    violation = asyncpg.UniqueViolationError("Synthetic conflict")
    violation.constraint_name = "users_email_key"
    monkeypatch.setattr(users_repo, "find_user_by_email", AsyncMock(return_value=None))
    monkeypatch.setattr(users_repo, "create_user", AsyncMock(side_effect=violation))
    monkeypatch.setattr(auth_service, "hash_password", Mock(return_value="synthetic-hash"))
    with pytest.raises(HTTPException) as error:
        await auth_service.signup("race@example.com", "valid-password")
    assert error.value.status_code == 409
    assert error.value.detail == "Email already registered"
    assert error.value.__cause__ is violation


@pytest.mark.parametrize("constraint", ["users_pkey", "unrelated_constraint", None])
async def test_unrelated_unique_violation_propagates(monkeypatch, constraint):
    violation = asyncpg.UniqueViolationError("Synthetic unrelated conflict")
    violation.constraint_name = constraint
    monkeypatch.setattr(users_repo, "find_user_by_email", AsyncMock(return_value=None))
    monkeypatch.setattr(users_repo, "create_user", AsyncMock(side_effect=violation))
    monkeypatch.setattr(auth_service, "hash_password", Mock(return_value="synthetic-hash"))
    with pytest.raises(asyncpg.UniqueViolationError) as error:
        await auth_service.signup("race@example.com", "valid-password")
    assert error.value is violation


async def test_other_database_failure_propagates(monkeypatch):
    failure = asyncpg.PostgresConnectionError("Synthetic database outage")
    monkeypatch.setattr(users_repo, "find_user_by_email", AsyncMock(return_value=None))
    monkeypatch.setattr(users_repo, "create_user", AsyncMock(side_effect=failure))
    monkeypatch.setattr(auth_service, "hash_password", Mock(return_value="synthetic-hash"))
    with pytest.raises(asyncpg.PostgresConnectionError) as error:
        await auth_service.signup("race@example.com", "valid-password")
    assert error.value is failure


@pytest.mark.parametrize("normalized_variants", [False, True])
async def test_concurrent_signup_one_created_one_conflict(monkeypatch, normalized_variants):
    await db.connect()
    email = f"race-{uuid4().hex}@example.com"
    emails = [email, f" {email.replace('@example.com', '@EXAMPLE.COM')} "] if normalized_variants else [email, email]
    passwords = ["first-valid-password", "second-valid-password"]
    barrier = asyncio.Barrier(2)
    original_lookup = users_repo.find_user_by_email

    async def synchronized_lookup(canonical):
        result = await original_lookup(canonical)
        assert result is None
        # Both real database pre-checks must see no user before either insert.
        await barrier.wait()
        return result

    try:
        with monkeypatch.context() as race_patch:
            race_patch.setattr(users_repo, "find_user_by_email", synchronized_lookup)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
            ) as client:
                responses = await asyncio.wait_for(asyncio.gather(*[
                    client.post("/api/auth/signup", json={"email": candidate, "password": password})
                    for candidate, password in zip(emails, passwords, strict=True)
                ]), timeout=10)
        assert sorted(response.status_code for response in responses) == [201, 409]
        winner_index = next(i for i, response in enumerate(responses) if response.status_code == 201)
        winner, rejected = responses[winner_index], responses[1 - winner_index]
        assert rejected.json() == {"detail": "Email already registered"}
        assert rejected.headers.get_list("set-cookie") == []
        assert settings.auth_cookie_name not in rejected.cookies
        rows = await db.fetch("SELECT id,email,password_hash FROM users WHERE email=$1", email)
        assert len(rows) == 1
        saved = rows[0]
        assert saved["email"] == email
        assert winner.json()["user"] == {"id": str(saved["id"]), "email": email}
        assert decode_token(winner.cookies[settings.auth_cookie_name]) == str(saved["id"])
        assert verify_password(passwords[winner_index], saved["password_hash"])
        assert not verify_password(passwords[1 - winner_index], saved["password_hash"])
    finally:
        await db.execute("DELETE FROM users WHERE email=$1", email)
        await db.disconnect()
