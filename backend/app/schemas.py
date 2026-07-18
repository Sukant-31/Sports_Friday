"""Pydantic request/response models."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


# --- auth ---
class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)


class UserOut(BaseModel):
    id: UUID
    email: EmailStr


# --- teams ---
class TeamOut(BaseModel):
    id: UUID
    external_id: str
    name: str
    league: str | None = None


# --- subscriptions ---
class SubscriptionCreate(BaseModel):
    team_id: UUID
    notify_goals: bool = True
    notify_cards: bool = False
    notify_match_status: bool = True


class SubscriptionUpdate(BaseModel):
    notify_goals: bool | None = None
    notify_cards: bool | None = None
    notify_match_status: bool | None = None


# --- push ---
class PushKeys(BaseModel):
    p256dh: str
    auth: str


class PushSubscribe(BaseModel):
    endpoint: str
    keys: PushKeys


class PushUnsubscribe(BaseModel):
    endpoint: str
