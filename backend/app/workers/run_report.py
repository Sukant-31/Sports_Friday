"""Safe operation counts for cron responses; exception messages stay in logs."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RunReport:
    operations: dict = field(default_factory=lambda: {
        stage: {'succeeded': 0, 'failed': 0}
        for stage in ('discovery', 'name_repair', 'polling', 'notifications')
    })
    failures: list = field(default_factory=list)
    fixtures_upserted: int = 0

    def record(self, stage: str, resource: str, error: Exception | None = None) -> None:
        self.operations[stage]['failed' if error else 'succeeded'] += 1
        if error:
            self.failures.append({'stage': stage, 'resource': str(resource),
                                  'error_type': type(error).__name__})

    def response(self, discovered: int) -> dict:
        return {'ok': not self.failures,
                'status': 'partial_failure' if self.failures else 'success',
                'discovered': discovered, 'operations': self.operations,
                'failures': self.failures}
