from __future__ import annotations

import asyncpg
from fastapi import HTTPException, status
from pydantic import ValidationError

from app.email_normalization import normalize_email
from app.repositories import users as users_repo
from app.security import hash_password, verify_password


async def signup(email: str, password: str) -> dict:
    email = normalize_email(email)
    if await users_repo.find_user_by_email(email):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    password_hash = hash_password(password)
    try:
        user = await users_repo.create_user(email, password_hash)
    except asyncpg.UniqueViolationError as exc:
        if exc.constraint_name != "users_email_key":
            raise
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered") from exc
    return {"id": user["id"], "email": user["email"]}


async def login(email: str, password: str) -> dict:
    user = await users_repo.find_user_by_email(email)
    if user is None:
        try:
            normalized = normalize_email(email)
        except ValidationError:
            # Existing legacy addresses can authenticate by exact lookup even
            # if they do not satisfy today's signup email validation rules.
            normalized = None
        if normalized is not None and normalized != email:
            user = await users_repo.find_user_by_email(normalized)
    if not user or not verify_password(password, user["password_hash"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    return {"id": user["id"], "email": user["email"]}
