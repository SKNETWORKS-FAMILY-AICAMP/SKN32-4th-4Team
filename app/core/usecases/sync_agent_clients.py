"""insurance_agent 정본과 insurance_real.ops.agent_client 미러 동기화."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.ports.insurance_repository import InsuranceAgentClientSnapshot


@dataclass(frozen=True)
class AgentClientMirrorReport:
    source_count: int
    target_count: int
    missing_in_real: tuple[str, ...]
    differing: tuple[str, ...]
    extra_in_real: tuple[str, ...]
    applied: bool = False
    disabled_extras: bool = False

    @property
    def in_sync(self) -> bool:
        return not self.missing_in_real and not self.differing and not self.extra_in_real

    def as_dict(self) -> dict[str, object]:
        return {
            "source_count": self.source_count,
            "target_count": self.target_count,
            "missing_in_real": list(self.missing_in_real),
            "differing": list(self.differing),
            "extra_in_real": list(self.extra_in_real),
            "in_sync": self.in_sync,
            "applied": self.applied,
            "disabled_extras": self.disabled_extras,
        }


_COMPARE_FIELDS = ("name", "api_key_hash", "rate_limit_rpm", "status")


class SyncAgentClients:
    """정본 읽기와 owner DSN 미러 변경을 하나의 명시적 작업으로 묶는다."""

    def __init__(self, source, target):
        self._source = source
        self._target = target

    @staticmethod
    def _source_map(rows: list[dict]) -> dict[str, dict]:
        return {str(row["agent_client_id"]): row for row in rows}

    @staticmethod
    def _target_map(
        rows: tuple[InsuranceAgentClientSnapshot, ...],
    ) -> dict[str, InsuranceAgentClientSnapshot]:
        return {row.agent_client_id: row for row in rows}

    @staticmethod
    def _diff(
        source: dict[str, dict], target: dict[str, InsuranceAgentClientSnapshot]
    ) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
        missing = tuple(sorted(set(source) - set(target)))
        extra = tuple(sorted(set(target) - set(source)))
        differing = tuple(
            sorted(
                client_id
                for client_id in set(source) & set(target)
                if any(
                    source[client_id][field]
                    != getattr(target[client_id], field)
                    for field in _COMPARE_FIELDS
                )
            )
        )
        return missing, differing, extra

    def run(
        self, *, apply: bool = False, disable_extras: bool = False
    ) -> AgentClientMirrorReport:
        source_rows = self._source.list_client_mirror_snapshots()
        source = self._source_map(source_rows)
        with self._target.transaction() as tx:
            target = self._target_map(tx.list_agent_clients())
            missing, differing, extra = self._diff(source, target)
            if apply:
                for client_id in sorted(set(missing) | set(differing)):
                    tx.sync_agent_client_mirror(**source[client_id])
                if disable_extras:
                    for client_id in extra:
                        tx.disable_agent_client(agent_client_id=client_id)
                target = self._target_map(tx.list_agent_clients())
                missing, differing, extra = self._diff(source, target)
        return AgentClientMirrorReport(
            source_count=len(source),
            target_count=len(target),
            missing_in_real=missing,
            differing=differing,
            extra_in_real=extra,
            applied=apply,
            disabled_extras=disable_extras,
        )


__all__ = ["AgentClientMirrorReport", "SyncAgentClients"]
