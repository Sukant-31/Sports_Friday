"""Shared password validation and compatibility with existing bcrypt hashes."""

from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import bcrypt
import httpx
import pytest
from pydantic import ValidationError

from app.main import create_app
from app.schemas import Credentials, LoginCredentials
from app.services import auth_service

ACCEPTED = [
    "abcdefgh",
    "normal-password",
    "a" * 72,
    "é" * 36,
    "😀" * 18,
    " " * 8,
    "  password  ",
    " e\u0301-password ",
]
REJECTED = ["", "a" * 7, "a" * 73, "a" * 200, "a" * 201, "é" * 37, "😀" * 19]


@pytest.mark.parametrize("model", [Credentials, LoginCredentials])
@pytest.mark.parametrize("password", ACCEPTED)
def test_supported_passwords_are_preserved(model, password):
    parsed = model(email="password-test@example.com", password=password)
    assert parsed.password == password
    assert parsed.password.encode("utf-8") == password.encode("utf-8")


@pytest.mark.parametrize("model", [Credentials, LoginCredentials])
@pytest.mark.parametrize("password", REJECTED)
def test_unsupported_passwords_are_rejected(model, password):
    with pytest.raises(ValidationError) as error:
        model(email="password-test@example.com", password=password)
    assert error.value.errors()[0]["loc"] == ("password",)


@pytest.mark.parametrize("path", ["/api/auth/signup", "/api/auth/login"])
@pytest.mark.parametrize("password", REJECTED)
async def test_http_validation_precedes_hashing_and_verification(monkeypatch, path, password):
    hashing = Mock(side_effect=AssertionError("Invalid password reached bcrypt"))
    lookup = AsyncMock(side_effect=AssertionError("Invalid password reached user lookup"))
    create = AsyncMock(side_effect=AssertionError("Invalid password reached user creation"))
    monkeypatch.setattr(auth_service, "hash_password", hashing)
    monkeypatch.setattr(auth_service, "verify_password", hashing)
    monkeypatch.setattr(auth_service.users_repo, "find_user_by_email", lookup)
    monkeypatch.setattr(auth_service.users_repo, "create_user", create)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        response = await client.post(
            path, json={"email": "password-test@example.com", "password": password}
        )
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "password"]
    hashing.assert_not_called()
    lookup.assert_not_awaited()
    create.assert_not_awaited()


@pytest.mark.parametrize("password", ["abcdefgh", "a" * 72, "é" * 36, "😀" * 18,
                                     " e\u0301-password "])
async def test_existing_bcrypt_passwords_still_authenticate(monkeypatch, password):
    # Create a stored hash using the existing algorithm/configuration directly.
    # The application must accept it without rewriting it or transforming input.
    stored_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode()
    user = {"id": uuid4(), "email": "password-test@example.com", "password_hash": stored_hash}
    lookup = AsyncMock(return_value=user)
    create = AsyncMock(side_effect=AssertionError("Login must not create or replace a user"))
    monkeypatch.setattr(auth_service.users_repo, "find_user_by_email", lookup)
    monkeypatch.setattr(auth_service.users_repo, "create_user", create)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/auth/login", json={"email": user["email"], "password": password}
        )
        wrong = await client.post(
            "/api/auth/login", json={"email": user["email"], "password": "wrong-password"}
        )
    assert response.status_code == 200
    assert response.json()["user"]["id"] == str(user["id"])
    assert wrong.status_code == 401
    assert user["password_hash"] == stored_hash
    create.assert_not_awaited()


async def test_signup_hashes_the_exact_password(monkeypatch):
    password = " e\u0301-password "
    uid = uuid4()
    create = AsyncMock(return_value={"id": uid, "email": "password-test@example.com"})
    monkeypatch.setattr(auth_service.users_repo, "find_user_by_email", AsyncMock(return_value=None))
    monkeypatch.setattr(auth_service.users_repo, "create_user", create)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/auth/signup", json={"email": "password-test@example.com", "password": password}
        )
    assert response.status_code == 201
    stored_hash = create.await_args.args[1].encode()
    assert bcrypt.checkpw(password.encode("utf-8"), stored_hash)
    assert not bcrypt.checkpw(password.strip().encode("utf-8"), stored_hash)
    assert not bcrypt.checkpw(password.replace("e\u0301", "é").encode("utf-8"), stored_hash)


@pytest.mark.parametrize("path", ["/api/auth/signup", "/api/auth/login"])
@pytest.mark.parametrize("body", [
    {"email": "password-test@example.com", "password": "SecretRejectedPassword" * 10},
    {"password": "SecretRejectedPassword"},
    {"email": "password-test@example.com", "password": {"secret": "SecretRejectedPassword"}},
])
async def test_validation_errors_never_echo_password_input(path, body, caplog):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        response = await client.post(path, json=body)
    assert response.status_code == 422
    assert "SecretRejectedPassword" not in response.text
    assert "SecretRejectedPassword" not in caplog.text
    assert all("input" not in error and "ctx" not in error
               for error in response.json()["detail"])


@pytest.mark.parametrize("password", ["normal-password", "a" * 72, "é" * 36, "😀" * 18])
async def test_signup_then_login_uses_only_bcrypt_hash(monkeypatch, password):
    stored = {}
    uid = uuid4()

    async def lookup(email):
        return stored.get(email)

    async def create(email, password_hash):
        assert password_hash != password
        assert password_hash.startswith("$2b$12$")
        user = {"id": uid, "email": email, "password_hash": password_hash}
        stored[email] = user
        return user

    monkeypatch.setattr(auth_service.users_repo, "find_user_by_email", lookup)
    monkeypatch.setattr(auth_service.users_repo, "create_user", create)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        body = {"email": "roundtrip@example.com", "password": password}
        signup = await client.post("/api/auth/signup", json=body)
        login = await client.post("/api/auth/login", json=body)
        wrong = await client.post("/api/auth/login", json={**body, "password": "wrong-password"})
    assert signup.status_code == 201
    assert login.status_code == 200
    assert signup.json()["user"] == login.json()["user"]
    assert wrong.status_code == 401
    for response in (signup, login, wrong):
        assert password not in response.text
        assert "password_hash" not in response.text


async def test_non_auth_validation_response_is_unchanged():
    app = create_app()

    @app.get("/validation-regression/{number}")
    async def read_number(number: int):
        return {"number": number}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/validation-regression/not-an-integer")
    assert response.status_code == 422
    assert response.json()["detail"][0]["input"] == "not-an-integer"
