"""Verified API-Football statuses and unchanged-window recovery regression."""

import pytest

from app import db
from app.repositories import matches as matches_repo
from app.sports_api.normalize import normalize_fixture, normalize_status
from app.workers.diff import diff_match
from app.workers.poller import process_fixture
from tests import test_pipeline

fixtures = test_pipeline.fixtures
infra = test_pipeline.infra

EXCEPTIONAL = {
    'PST': 'postponed', 'CANC': 'cancelled', 'ABD': 'abandoned',
    'SUSP': 'suspended', 'INT': 'interrupted', 'TBD': 'time_to_be_defined',
    'AWD': 'awarded', 'WO': 'walkover',
}


def snapshot(code, score=0):
    return normalize_fixture({
        'fixture': {'id': 'status-regression', 'status': {'short': code, 'elapsed': 30}},
        'teams': {'home': {'id': 100, 'name': 'Home'}, 'away': {'id': 200, 'name': 'Away'}},
        'goals': {'home': score, 'away': 0},
    })


@pytest.mark.parametrize('code,status', EXCEPTIONAL.items())
def test_explicit_exceptional_mapping(code, status):
    assert normalize_status(code) == status
    assert normalize_fixture({'fixture': {'id': 1, 'status': {'short': code}},
                              'teams': {'home': {'id': 100}, 'away': {'id': 200}},
                              'goals': {}})['status'] == status
    assert status not in ('live', 'finished', 'scheduled')


@pytest.mark.parametrize('code', [None, '', 'FUTURE_CODE', 'DELAYED'])
def test_unknown_codes_fail_closed_without_inventing_delay_code(code):
    assert normalize_status(code) == 'unknown'


@pytest.mark.parametrize('code', ['FT', 'AET', 'PEN'])
def test_normal_finished_mapping_and_full_time_unchanged(code):
    assert normalize_status(code) == 'finished'
    assert [event['type'] for event in diff_match(snapshot('1H'), snapshot(code))] == ['full_time']


@pytest.mark.parametrize('code', ['1H', '2H', 'HT', 'ET', 'BT', 'P', 'LIVE'])
def test_normal_live_mapping_and_kickoff_unchanged(code):
    assert normalize_status(code) == 'live'
    assert [event['type'] for event in diff_match(snapshot('NS'), snapshot(code))] == ['kickoff']


@pytest.mark.parametrize('code', [*EXCEPTIONAL, 'FUTURE_CODE'])
def test_exceptional_result_does_not_generate_normal_events_or_repeat(code):
    exceptional = snapshot(code, score=3)
    assert diff_match(snapshot('1H'), exceptional) == []
    assert diff_match(None, exceptional) == []
    assert diff_match(exceptional, exceptional) == []


@pytest.mark.parametrize('code', ['SUSP', 'INT', 'ABD', 'AWD', 'WO', 'CANC', 'FUTURE_CODE'])
def test_exceptional_recovery_is_not_a_new_kickoff(code):
    assert diff_match(snapshot(code), snapshot('2H')) == []


@pytest.mark.parametrize('code', ['PST', 'TBD'])
def test_confirmed_start_after_pre_match_exception_can_notify(code):
    assert [event['type'] for event in diff_match(snapshot(code), snapshot('1H'))] == ['kickoff']


@pytest.mark.parametrize('code', [*EXCEPTIONAL, 'FUTURE_CODE'])
async def test_persisted_exception_repeated_polling_and_recovery_eligibility(fixtures, code):
    match = fixtures['match']
    exceptional = {**snapshot(code), 'external_id': match['external_id']}
    jobs = []

    async def notify(payload):
        jobs.append(payload)

    await process_fixture(notify, match, exceptional)
    await process_fixture(notify, match, exceptional)
    assert jobs == []
    stored = await db.fetchrow('SELECT * FROM matches WHERE id=$1', match['id'])
    assert stored['status'] == normalize_status(code)
    # Existing unknown-kickoff eligibility is preserved; no new polling cadence.
    assert any(row['id'] == match['id'] for row in await matches_repo.find_pollable_matches())
    assert any(row['id'] == match['id']
               for row in await matches_repo.find_live_matches_for_user(fixtures['user']['id']))
