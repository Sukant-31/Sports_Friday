"""Polling events must use existing teams without overwriting their metadata."""

from unittest.mock import AsyncMock
from uuid import uuid4

from app.repositories import teams
from app.sports_api.mock import MockSportsApiClient, build_timeline
from app.workers import poller


async def test_polling_preserves_team_metadata_and_routes_events(monkeypatch):
    match = {
        "id": uuid4(), "external_id": "team-identity",
        "home_team_id": uuid4(), "away_team_id": uuid4(),
        "status": "scheduled", "home_score": 0, "away_score": 0,
    }
    metadata_write = AsyncMock(side_effect=AssertionError("polling changed team metadata"))
    monkeypatch.setattr(teams, "upsert_team", metadata_write)
    monkeypatch.setattr(poller, "get_match_state", AsyncMock(return_value=None))
    monkeypatch.setattr(poller, "set_match_state", AsyncMock())
    monkeypatch.setattr(poller.matches_repo, "update_match_state", AsyncMock())
    record = AsyncMock(return_value={"id": uuid4()})
    monkeypatch.setattr(poller.events_repo, "record_event_if_new", record)
    notify = AsyncMock()
    client = MockSportsApiClient(build_timeline(match["external_id"]))

    await poller.poll_match(client, notify, match)
    assert [call.args[1] for call in record.await_args_list] == [
        match["home_team_id"], match["away_team_id"],
    ]
    assert [call.args[0]["team_id"] for call in notify.await_args_list] == [
        str(match["home_team_id"]), str(match["away_team_id"]),
    ]

    record.reset_mock()
    notify.reset_mock()
    match["status"] = "live"
    client.advance()
    await poller.poll_match(client, notify, match)
    assert record.await_args.args[1] == match["home_team_id"]
    assert notify.await_args.args[0]["type"] == "goal"
    assert notify.await_args.args[0]["team_id"] == str(match["home_team_id"])
    metadata_write.assert_not_awaited()
