"""Pydantic request/response models."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field
from pydantic.alias_generators import to_camel

from app.push_destination import validate_push_destination
from app.push_registration_limits import endpoint_bytes, key_bytes


# --- auth ---
def _validate_password_bytes(password: str) -> str:
    if len(password.encode("utf-8")) > 72:
        raise ValueError("Password must be at most 72 UTF-8 bytes")
    return password


Password = Annotated[str, Field(min_length=8), AfterValidator(_validate_password_bytes)]


class Credentials(BaseModel):
    email: EmailStr
    password: Password


class LoginCredentials(BaseModel):
    # Plain str, not EmailStr: login only looks up an existing user, so it
    # shouldn't reject accounts whose stored email fails today's format rules
    # (e.g. the seeded demo@local).
    email: str
    password: Password


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
    notify_cards: bool = True
    notify_match_status: bool = True


class SubscriptionUpdate(_CamelModel):
    notify_goals: bool | None = None
    notify_cards: bool | None = None
    notify_match_status: bool | None = None


# --- push ---
class PushKeys(BaseModel):
    p256dh: Annotated[str, AfterValidator(key_bytes)]
    auth: Annotated[str, AfterValidator(key_bytes)]


PushDestination = Annotated[str, AfterValidator(validate_push_destination)]


class PushSubscribe(BaseModel):
    endpoint: Annotated[str, AfterValidator(endpoint_bytes), AfterValidator(validate_push_destination)]
    keys: PushKeys


class PushUnsubscribe(BaseModel):
    endpoint: str


class PushTest(BaseModel):
    endpoint: PushDestination
