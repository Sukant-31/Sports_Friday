from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.schemas import SubscriptionCreate
from app.services import subscription_service as svc
from app.sports_api.mock import MockSportsApiClient, build_timeline
from app.workers import discovery


async def test_follow_discovers_fixtures_before_returning(monkeypatch):
    team_id = uuid4()
    row = {"id": uuid4(), "team_id": team_id}
    monkeypatch.setattr(svc.teams_repo, "find_team_by_id", AsyncMock(
        return_value={"id": team_id, "external_id": "100"},
    ))
    monkeypatch.setattr(svc.subs_repo, "create_subscription", AsyncMock(return_value=row))
    monkeypatch.setattr(discovery.teams_repo, "upsert_team", AsyncMock(
        side_effect=[{"id": team_id}, {"id": uuid4()}],
    ))
    upsert_match = AsyncMock()
    monkeypatch.setattr(discovery.matches_repo, "upsert_match", upsert_match)
    client = MockSportsApiClient(build_timeline("follow-fixture"))
    client.get_team_fixtures = AsyncMock(wraps=client.get_team_fixtures)

    result = await svc.create(uuid4(), SubscriptionCreate(team_id=team_id), client)

    assert result == {**row, "warning": None}
    client.get_team_fixtures.assert_awaited_once_with("100", discovery.settings.fixtures_lookahead)
    upsert_match.assert_awaited_once()
    assert upsert_match.await_args.args[0] == "follow-fixture"


async def test_discovery_failure_preserves_follow(monkeypatch):
    team_id = uuid4()
    row = {"id": uuid4(), "team_id": team_id}
    monkeypatch.setattr(svc.teams_repo, "find_team_by_id", AsyncMock(
        return_value={"id": team_id, "external_id": "100"},
    ))
    monkeypatch.setattr(svc.subs_repo, "create_subscription", AsyncMock(return_value=row))
    monkeypatch.setattr(svc, "discover_team", AsyncMock(side_effect=RuntimeError("unavailable")))
    result = await svc.create(uuid4(), SubscriptionCreate(team_id=team_id), object())
    assert result["id"] == row["id"]
    assert "could not be refreshed" in result["warning"]


async def test_unknown_team_does_not_discover_or_follow(monkeypatch):
    monkeypatch.setattr(svc.teams_repo, "find_team_by_id", AsyncMock(return_value=None))
    create = AsyncMock()
    discover = AsyncMock()
    monkeypatch.setattr(svc.subs_repo, "create_subscription", create)
    monkeypatch.setattr(svc, "discover_team", discover)
    with pytest.raises(HTTPException) as exc:
        await svc.create(uuid4(), SubscriptionCreate(team_id=uuid4()), object())
    assert exc.value.status_code == 404
    create.assert_not_awaited()
    discover.assert_not_awaited()


async def test_discovery_includes_ongoing_match_and_prefers_live_snapshot(monkeypatch):
    client = MockSportsApiClient(build_timeline("ongoing"))
    client.get_team_live_fixtures = AsyncMock(return_value=build_timeline("ongoing")[0][1])
    monkeypatch.setattr(discovery.teams_repo, "upsert_team", AsyncMock(
        side_effect=[{"id": uuid4()}, {"id": uuid4()}],
    ))
    upsert = AsyncMock()
    monkeypatch.setattr(discovery.matches_repo, "upsert_match", upsert)
    assert await discovery.discover_team(client, "100") == 1
    assert upsert.await_args.args[3] == "live"
