"""Decide from saved state when a followed fixture deserves an API refresh."""
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.config import settings


@dataclass(frozen=True)
class PollDecision:
    priority: str
    reason: str
    interval_seconds: int


def decide(match, now: datetime) -> PollDecision | None:
    status = match['status']
    if status == 'finished':
        return None
    start = match.get('starts_at')
    if status == 'live' and start is None:
        decision = PollDecision('high', 'live', settings.live_poll_interval_seconds)
    elif start is None:
        decision = PollDecision('low', 'unknown_kickoff', settings.unresolved_poll_interval_seconds)
    elif status == 'live':
        if now > start + timedelta(hours=settings.status_recovery_hours):
            return None
        end = start + timedelta(minutes=settings.expected_match_minutes + settings.post_match_grace_minutes)
        if now <= end:
            decision = PollDecision('high', 'live', settings.live_poll_interval_seconds)
        else:
            decision = PollDecision('high', 'unresolved_final_status', settings.unresolved_poll_interval_seconds)
    elif (start > now + timedelta(minutes=settings.pre_match_window_minutes)
          or start < now - timedelta(hours=settings.status_recovery_hours)):
        return None
    elif now < start:
        decision = PollDecision('high', 'near_kickoff', settings.pre_match_poll_interval_seconds)
    elif now <= start + timedelta(minutes=settings.pre_match_window_minutes):
        decision = PollDecision('high', 'active_window', settings.live_poll_interval_seconds)
    elif now <= start + timedelta(minutes=settings.expected_match_minutes):
        decision = PollDecision('high', 'unresolved_start', settings.pre_match_poll_interval_seconds)
    elif now <= start + timedelta(minutes=settings.expected_match_minutes + settings.post_match_grace_minutes):
        decision = PollDecision('medium', 'final_confirmation', settings.live_poll_interval_seconds)
    else:
        decision = PollDecision('high', 'unresolved_final_status', settings.unresolved_poll_interval_seconds)
    checked = match.get('last_checked_at') or match.get('last_polled_at')
    if checked is not None and now - checked < timedelta(seconds=decision.interval_seconds):
        return None
    return decision
