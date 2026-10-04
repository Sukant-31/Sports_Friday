"""Safe cron summaries: categories and counters, never exception messages."""
from __future__ import annotations

from dataclasses import dataclass, field

import asyncpg

from app.sports_api.client import SportsApiError


@dataclass
class RunReport:
    operations: dict = field(default_factory=lambda: {
        stage: {'succeeded': 0, 'failed': 0}
        for stage in ('discovery', 'name_repair', 'polling', 'notifications', 'database')
    })
    failures: list = field(default_factory=list)
    fixtures_upserted: int = 0
    followed_teams: int = 0
    stored_fixtures: int = 0
    live_fixtures: int = 0
    fixtures_requiring_polling: int = 0
    fixture_priorities: dict = field(default_factory=dict)
    notifications_generated: int = 0
    # Legacy internal name: counts provider acceptance receipts, never display.
    notifications_delivered: int = 0
    api_requests: dict = field(default_factory=dict)
    api_budget: dict = field(default_factory=dict)
    skip_reason: str | None = None

    def record(self, stage: str, resource: str, error: Exception | None = None) -> None:
        self.operations[stage]['failed' if error else 'succeeded'] += 1
        if error:
            category = ('notification_failure' if stage == 'notifications' else
                        'api_football_failure' if isinstance(error, SportsApiError) else
                        'database_failure' if stage == 'database' or isinstance(error, asyncpg.PostgresError)
                        else 'processing_failure')
            self.failures.append({'stage': stage, 'resource': str(resource),
                                  'error_type': type(error).__name__, 'category': category})

    def response(self, discovered: int) -> dict:
        reasons = self.api_requests.get('skip_reasons', {})
        status = 'success'
        if self.failures:
            status = ('database_failure' if any(f['category'] == 'database_failure'
                                               for f in self.failures) else 'partial_failure')
        elif self.skip_reason:
            status = 'skipped'
        elif reasons.get('budget_exhausted'):
            status = 'budget_exhausted'
        elif reasons.get('priority_reserve'):
            status = 'priority_reserve'
        elif reasons.get('concurrent_request'):
            status = 'skipped'
            self.skip_reason = 'concurrent_request'
        elif not self.fixtures_requiring_polling and not discovered:
            status = 'nothing_due'
        return {'ok': not self.failures, 'status': status, 'skip_reason': self.skip_reason,
                'discovered': discovered, 'operations': self.operations, 'failures': self.failures,
                'followed_teams': self.followed_teams, 'stored_fixtures': self.stored_fixtures,
                'live_fixtures': self.live_fixtures,
                'fixtures_requiring_polling': self.fixtures_requiring_polling,
                'fixture_priorities': self.fixture_priorities,
                'api_requests': self.api_requests, 'api_budget': self.api_budget,
                'notifications': {'generated': self.notifications_generated,
                                  'accepted': self.notifications_delivered,
                                  'display_confirmation': 'unavailable',
                                  'failed_operations': self.operations['notifications']['failed']}}
