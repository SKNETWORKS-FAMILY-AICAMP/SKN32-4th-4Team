from __future__ import annotations

from contextlib import contextmanager

from app.core.ports.insurance_repository import InsuranceAgentClientSnapshot
from app.core.usecases.sync_agent_clients import SyncAgentClients


class _Source:
    def __init__(self):
        self.rows = [
            {
                "agent_client_id": "ac-one",
                "name": "One",
                "api_key_hash": "h1",
                "rate_limit_rpm": 10,
                "status": "active",
            },
            {
                "agent_client_id": "ac-two",
                "name": "Two",
                "api_key_hash": "h2",
                "rate_limit_rpm": 20,
                "status": "disabled",
            },
        ]

    def list_client_mirror_snapshots(self):
        return list(self.rows)


class _Tx:
    def __init__(self, rows):
        self.rows = {row.agent_client_id: row for row in rows}

    def list_agent_clients(self):
        return tuple(self.rows.values())

    def sync_agent_client_mirror(self, **row):
        self.rows[row["agent_client_id"]] = InsuranceAgentClientSnapshot(**row)

    def disable_agent_client(self, *, agent_client_id):
        current = self.rows[agent_client_id]
        self.rows[agent_client_id] = InsuranceAgentClientSnapshot(
            agent_client_id=current.agent_client_id,
            name=current.name,
            api_key_hash=current.api_key_hash,
            rate_limit_rpm=current.rate_limit_rpm,
            status="disabled",
        )


class _Target:
    def __init__(self, rows):
        self.tx = _Tx(rows)

    @contextmanager
    def transaction(self):
        yield self.tx


def test_sync_agent_clients_dry_run_is_non_mutating():
    source = _Source()
    target = _Target(
        [
            InsuranceAgentClientSnapshot("ac-one", "Old", "old", 10, "active"),
            InsuranceAgentClientSnapshot("ac-extra", "Extra", "h3", 30, "active"),
        ]
    )

    report = SyncAgentClients(source, target).run()

    assert report.missing_in_real == ("ac-two",)
    assert report.differing == ("ac-one",)
    assert report.extra_in_real == ("ac-extra",)
    assert report.applied is False
    assert target.tx.rows["ac-one"].name == "Old"


def test_sync_agent_clients_apply_can_disable_extras():
    source = _Source()
    target = _Target(
        [InsuranceAgentClientSnapshot("ac-extra", "Extra", "h3", 30, "active")]
    )

    report = SyncAgentClients(source, target).run(apply=True, disable_extras=True)

    assert report.in_sync is False
    assert report.applied is True
    assert target.tx.rows["ac-one"].api_key_hash == "h1"
    assert target.tx.rows["ac-extra"].status == "disabled"
