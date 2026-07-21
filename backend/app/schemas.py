"""Pydantic request/response models."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field
from pydantic.alias_generators import to_camel


# --- auth ---
class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=200)


class LoginCredentials(BaseModel):
    # Plain str, not EmailStr: login only looks up an existing user, so it
    # shouldn't reject accounts whose stored email fails today's format rules
    # (e.g. the seeded demo@local).
    email: str
    password: str = Field(min_length=8, max_length=200)


class UserOut(BaseModel):
    id: UUID
    email: str


# --- teams ---
class TeamOut(BaseModel):
    id: UUID
    external_id: str
    name: str
    league: str | None = None


# --- subscriptions ---
# The frontend sends camelCase bodies (teamId, notifyGoals, ...); accept those
# via alias while keeping snake_case attribute names for the rest of the code.
class _CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class SubscriptionCreate(_CamelModel):
    team_id: UUID
    notify_goals: bool = True
    notify_cards: bool = False
    notify_match_status: bool = True


class SubscriptionUpdate(_CamelModel):
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
