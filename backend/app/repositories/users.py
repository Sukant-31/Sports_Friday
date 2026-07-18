from __future__ import annotations

import asyncpg

from app import db


async def create_user(email: str, password_hash: str) -> asyncpg.Record:
    return await db.fetchrow(
        """
        INSERT INTO users (email, password_hash)
        VALUES ($1, $2)
        RETURNING id, email, created_at
        """,
        email,
        password_hash,
    )


async def find_user_by_email(email: str) -> asyncpg.Record | None:
    return await db.fetchrow(
        "SELECT id, email, password_hash, created_at FROM users WHERE email = $1",
        email,
    )


async def find_user_by_id(user_id: str) -> asyncpg.Record | None:
    return await db.fetchrow(
        "SELECT id, email, created_at FROM users WHERE id = $1", user_id
    )
