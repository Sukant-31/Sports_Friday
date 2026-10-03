"""Canonical email lookup without rewriting or merging existing accounts."""

from unittest.mock import AsyncMock, Mock, call
from uuid import uuid4

import bcrypt
import httpx
import pytest
from fastapi import HTTPException

from app.email_normalization import normalize_email
from app.main import create_app
from app.schemas import Credentials
from app.services import auth_service

VARIANTS = [
    ("Alice@EXAMPLE.COM", "Alice@example.com"),
    (" \tAlice@example.com\n", "Alice@example.com"),
    ("e\u0301@example.com", "é@example.com"),
    ("Alice@xn--bcher-kva.de", "Alice@bücher.de"),
    ("Alice@BÜCHER.DE", "Alice@bücher.de"),
]


@pytest.mark.parametrize("original,canonical", VARIANTS)
def test_shared_normalization_matches_signup(original, canonical):
    assert normalize_email(original) == canonical
    assert Credentials(email=original, password="valid-password").email == canonical


def test_local_part_case_is_preserved():
    assert normalize_email("Alice@EXAMPLE.COM") == "Alice@example.com"
    assert normalize_email("alice@EXAMPLE.COM") == "alice@example.com"


@pytest.mark.parametrize("original,canonical", VARIANTS)
async def test_signup_checks_and_stores_canonical_email(monkeypatch, original, canonical):
    uid = uuid4()
    lookup = AsyncMock(return_value=None)
    create = AsyncMock(return_value={"id": uid, "email": canonical})
    monkeypatch.setattr(auth_service.users_repo, "find_user_by_email", lookup)
    monkeypatch.setattr(auth_service.users_repo, "create_user", create)
    monkeypatch.setattr(auth_service, "hash_password", Mock(return_value="synthetic-hash"))
    result = await auth_service.signup(original, "valid-password")
    lookup.assert_awaited_once_with(canonical)
    create.assert_awaited_once_with(canonical, "synthetic-hash")
    assert result == {"id": uid, "email": canonical}


@pytest.mark.parametrize("original,canonical", VARIANTS)
async def test_canonical_duplicate_signup_rejected(monkeypatch, original, canonical):
    lookup = AsyncMock(return_value={"id": uuid4(), "email": canonical})
    create = AsyncMock()
    hashing = Mock()
    monkeypatch.setattr(auth_service.users_repo, "find_user_by_email", lookup)
    monkeypatch.setattr(auth_service.users_repo, "create_user", create)
    monkeypatch.setattr(auth_service, "hash_password", hashing)
    with pytest.raises(HTTPException) as error:
        await auth_service.signup(original, "valid-password")
    assert error.value.status_code == 409
    lookup.assert_awaited_once_with(canonical)
    create.assert_not_awaited()
    hashing.assert_not_called()


@pytest.mark.parametrize("original,canonical", VARIANTS)
async def test_login_tries_original_then_canonical(monkeypatch, original, canonical):
    user = {"id": uuid4(), "email": canonical, "password_hash": "synthetic-hash"}
    unchanged = dict(user)
    lookup = AsyncMock(side_effect=[None, user])
    verify = Mock(return_value=True)
    monkeypatch.setattr(auth_service.users_repo, "find_user_by_email", lookup)
    monkeypatch.setattr(auth_service, "verify_password", verify)
    result = await auth_service.login(original, "valid-password")
    assert lookup.await_args_list == [call(original), call(canonical)]
    verify.assert_called_once_with("valid-password", unchanged["password_hash"])
    assert result == {"id": unchanged["id"], "email": unchanged["email"]}
    assert user == unchanged


@pytest.mark.parametrize("email", ["demo@local", "Alice@EXAMPLE.COM", " Alice@example.com "])
async def test_exact_match_takes_precedence_including_legacy(monkeypatch, email):
    user = {"id": uuid4(), "email": email, "password_hash": "legacy-hash"}
    lookup = AsyncMock(return_value=user)
    monkeypatch.setattr(auth_service.users_repo, "find_user_by_email", lookup)
    monkeypatch.setattr(auth_service, "verify_password", Mock(return_value=True))
    result = await auth_service.login(email, "valid-password")
    lookup.assert_awaited_once_with(email)
    assert result == {"id": user["id"], "email": email}
    assert user["password_hash"] == "legacy-hash"


async def test_wrong_exact_password_does_not_try_another_account(monkeypatch):
    email = "Alice@EXAMPLE.COM"
    exact = {"id": uuid4(), "email": email, "password_hash": "legacy-hash"}
    lookup = AsyncMock(return_value=exact)
    monkeypatch.setattr(auth_service.users_repo, "find_user_by_email", lookup)
    monkeypatch.setattr(auth_service, "verify_password", Mock(return_value=False))
    with pytest.raises(HTTPException) as error:
        await auth_service.login(email, "wrong-password")
    assert error.value.status_code == 401
    lookup.assert_awaited_once_with(email)


@pytest.mark.parametrize("email", ["missing@example.com", "missing@local", "not an email"])
async def test_missing_account_still_returns_401_without_duplicate_lookup(monkeypatch, email):
    lookup = AsyncMock(return_value=None)
    verify = Mock()
    monkeypatch.setattr(auth_service.users_repo, "find_user_by_email", lookup)
    monkeypatch.setattr(auth_service, "verify_password", verify)
    with pytest.raises(HTTPException) as error:
        await auth_service.login(email, "valid-password")
    assert error.value.status_code == 401
    lookup.assert_awaited_once_with(email)
    verify.assert_not_called()


async def test_local_part_case_does_not_select_different_account(monkeypatch):
    lookup = AsyncMock(return_value=None)
    monkeypatch.setattr(auth_service.users_repo, "find_user_by_email", lookup)
    with pytest.raises(HTTPException) as error:
        await auth_service.login("alice@EXAMPLE.COM", "valid-password")
    assert error.value.status_code == 401
    assert lookup.await_args_list == [call("alice@EXAMPLE.COM"), call("alice@example.com")]


async def test_http_login_preserves_existing_account_and_hash(monkeypatch):
    password = "valid-password"
    stored_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()
    user = {"id": uuid4(), "email": "Alice@example.com", "password_hash": stored_hash}
    unchanged = dict(user)
    lookup = AsyncMock(side_effect=lambda email: user if email == user["email"] else None)
    create = AsyncMock(side_effect=AssertionError("Login must not write an account"))
    monkeypatch.setattr(auth_service.users_repo, "find_user_by_email", lookup)
    monkeypatch.setattr(auth_service.users_repo, "create_user", create)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/auth/login", json={"email": " Alice@EXAMPLE.COM ", "password": password}
        )
        wrong = await client.post(
            "/api/auth/login", json={"email": " Alice@EXAMPLE.COM ", "password": "wrong-password"}
        )
    assert response.status_code == 200
    assert response.json()["user"] == {"id": str(user["id"]), "email": user["email"]}
    assert wrong.status_code == 401
    assert user == unchanged
    create.assert_not_awaited()
