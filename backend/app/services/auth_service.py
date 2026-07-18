from __future__ import annotations

from fastapi import HTTPException, status

from app.repositories import users as users_repo
from app.security import hash_password, verify_password


async def signup(email: str, password: str) -> dict:
    if await users_repo.find_user_by_email(email):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    user = await users_repo.create_user(email, hash_password(password))
    return {"id": user["id"], "email": user["email"]}


async def login(email: str, password: str) -> dict:
    user = await users_repo.find_user_by_email(email)
    if not user or not verify_password(password, user["password_hash"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    return {"id": user["id"], "email": user["email"]}
