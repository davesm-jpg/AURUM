"""Synthetic HTTP and clock fixtures. No live official requests in this module."""

import json
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from email.message import Message
from pathlib import Path
from sys import executable
from threading import Event
from typing import Any
from urllib.error import HTTPError

import pytest
from test_economic_archive import EARLY, SyntheticResponse, route_synthetic, synthetic_ics

from aurum import cli, economic_polling
from aurum.economic_archive import (
    EconomicPublisher,
    EconomicReceipt,
    capture_official_ics,
    economic_receipts,
)
from aurum.economic_polling import (
    EconomicPollConfig,
    describe_economic_poller,
    economic_poll_attempts,
    economic_pollers,
    poll_economic_calendar,
    register_economic_poller,
)
from aurum.storage import SCHEMA_VERSION, DatasetStore
from aurum.storage_admin import backup_store, verify_store


@dataclass
class SyntheticClock:
    at: datetime = EARLY

    def __call__(self) -> datetime:
        return self.at


def config() -> EconomicPollConfig:
    return EconomicPollConfig(EconomicPublisher.BEA, max_retry_seconds=1200)


def test_polling_migrates_v15_and_preserves_restart_cadence_and_source_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = DatasetStore(tmp_path / "synthetic-poll.sqlite")
    with store.connection(write=True) as db:
        db.execute("DROP TABLE economic_poll_attempts")
        db.execute("DROP TABLE economic_pollers")
        db.execute("PRAGMA user_version = 15")
    assert economic_pollers(store) == []
    clock = SyntheticClock()
    reference = register_economic_poller(store, config(), now=clock())
    route_synthetic(
        monkeypatch, EconomicPublisher.BEA, SyntheticResponse(synthetic_ics("20250310T123000Z"))
    )
    first = poll_economic_calendar(store, reference, clock=clock)
    assert first["attempted"] is True and first["state"] == "captured"
    assert first["review_required"] is True and first["automatic_monitor_adoption"] is False
    assert len(economic_receipts(store)) == 1
    assert register_economic_poller(store, config(), now=clock()) == reference
    assert (
        poll_economic_calendar(DatasetStore(store.path), reference, clock=clock)["attempted"]
        is False
    )
    clock.at += timedelta(seconds=config().interval_seconds)
    assert describe_economic_poller(store, reference, now=clock())["state"] == "capture_overdue"
    second = poll_economic_calendar(store, reference, clock=clock)
    assert second["last_capture_id"] != first["last_capture_id"]
    assert len(economic_poll_attempts(store, reference, after=1)) == 1
    with store.connection() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert db.execute("SELECT count(*) FROM source_blobs").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM monitor_runs").fetchone()[0] == 0
    audit = verify_store(store, verify_normalized=True)
    assert audit["normalized"] == {
        "datasets_verified": 0,
        "quote_series_verified": 0,
        "datasets_blocked_for_replay": 0,
        "economic_captures_verified": 2,
        "economic_pollers_verified": 1,
        "economic_poll_attempts_verified": 2,
    }
    api = pytest.importorskip("aurum.api")
    http = pytest.importorskip("fastapi.testclient")
    with http.TestClient(api.create_app(store, "synthetic-token")) as client:
        route = "/api/economic-pollers"
        assert client.get(route).status_code == 401
        client.headers["Authorization"] = "Bearer synthetic-token"
        assert client.get(route).json()[0]["poller_id"] == reference
        assert (
            client.get(f"{route}/{reference}").json()["last_capture_id"]
            == second["last_capture_id"]
        )
        assert len(client.get(f"{route}/{reference}/attempts?after=1").json()) == 1


def test_failed_poll_retains_prior_capture_and_bounded_backoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = DatasetStore(tmp_path / "synthetic-errors.sqlite")
    clock = SyntheticClock()
    reference = register_economic_poller(store, config(), now=clock())
    route_synthetic(
        monkeypatch, EconomicPublisher.BEA, SyntheticResponse(synthetic_ics("20250310T123000Z"))
    )
    captured = poll_economic_calendar(store, reference, clock=clock)["last_capture_id"]
    clock.at += timedelta(hours=1)
    route_synthetic(
        monkeypatch,
        EconomicPublisher.BEA,
        HTTPError(EconomicPublisher.BEA.url, 403, "private failure body", Message(), None),
    )
    for failures, delay in ((1, 300), (2, 600), (3, 1200), (4, 1200)):
        status = poll_economic_calendar(store, reference, clock=clock)
        assert status["state"] == "failed" and status["consecutive_failures"] == failures
        assert status["last_capture_id"] == captured
        assert status["next_due_at"] == (clock() + timedelta(seconds=delay)).isoformat()
        latest = status["latest_attempt"]
        assert isinstance(latest, dict) and latest["error_code"] == "http_403"
        assert "private failure body" not in json.dumps(status)
        assert poll_economic_calendar(store, reference, clock=clock)["attempted"] is False
        clock.at += timedelta(seconds=delay)
    assert len(economic_receipts(store)) == 1
    route_synthetic(
        monkeypatch, EconomicPublisher.BEA, SyntheticResponse(b"<html>synthetic outage</html>")
    )
    invalid = poll_economic_calendar(store, reference, clock=clock)
    latest = invalid["latest_attempt"]
    assert isinstance(latest, dict) and latest["error_code"] == "capture_failed"


def test_concurrent_poller_claims_do_not_duplicate_public_requests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = DatasetStore(tmp_path / "synthetic-concurrent.sqlite")
    clock = SyntheticClock()
    reference = register_economic_poller(store, config(), now=clock())
    route_synthetic(
        monkeypatch, EconomicPublisher.BEA, SyntheticResponse(synthetic_ics("20250310T123000Z"))
    )
    entered, release = Event(), Event()
    original = capture_official_ics

    def hold(*args: Any, **kwargs: Any) -> EconomicReceipt:
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(economic_polling, "capture_official_calendar", hold)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(poll_economic_calendar, store, reference, clock=clock)
        assert entered.wait(5)
        other = poll_economic_calendar(store, reference, clock=clock)
        assert other["attempted"] is False and other["state"] == "fetching"
        release.set()
        assert first.result(timeout=5)["state"] == "captured"
    assert len(economic_poll_attempts(store, reference)) == 1
    assert len(economic_receipts(store)) == 1


def test_restart_after_receipt_before_ack_preserves_capture_and_marks_lease_uncertain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = DatasetStore(tmp_path / "synthetic-interrupted.sqlite")
    clock = SyntheticClock()
    reference = register_economic_poller(store, config(), now=clock())
    route_synthetic(
        monkeypatch, EconomicPublisher.BEA, SyntheticResponse(synthetic_ics("20250310T123000Z"))
    )
    original = capture_official_ics

    def crash(*args: Any, **kwargs: Any) -> EconomicReceipt:
        original(*args, **kwargs)
        raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(economic_polling, "capture_official_calendar", crash)
        with pytest.raises(KeyboardInterrupt):
            poll_economic_calendar(store, reference, clock=clock)
    assert len(economic_receipts(store)) == 1
    assert poll_economic_calendar(store, reference, clock=clock)["attempted"] is False
    clock.at += timedelta(seconds=config().lease_seconds)
    assert describe_economic_poller(store, reference, now=clock())["state"] == "interrupted"
    expired = poll_economic_calendar(store, reference, clock=clock)
    assert expired["state"] == "uncertain" and expired["last_capture_id"] is None
    clock.at = datetime.fromisoformat(str(expired["next_due_at"]))
    assert poll_economic_calendar(store, reference, clock=clock)["state"] == "captured"
    assert [attempt["outcome"] for attempt in economic_poll_attempts(store, reference)] == [
        "uncertain",
        "captured",
    ]


def test_invalid_poll_config_and_definition_tampering_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        replace(config(), interval_seconds=0)
    with pytest.raises(ValueError):
        replace(config(), lease_seconds=True)
    store = DatasetStore(tmp_path / "synthetic-invalid.sqlite")
    with pytest.raises(ValueError, match="aware UTC"):
        register_economic_poller(store, config(), now=EARLY.replace(tzinfo=None))
    assert not store.path.exists()
    reference = register_economic_poller(store, config(), now=EARLY)
    with pytest.raises(ValueError, match="precedes registration"):
        poll_economic_calendar(store, reference, clock=lambda: EARLY - timedelta(seconds=1))
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE economic_pollers SET definition = replace(definition, '3600', '7200')")
    with pytest.raises(ValueError, match="immutable identity"):
        poll_economic_calendar(store, reference, clock=lambda: EARLY)


def test_poll_cli_uses_foreground_lifecycle_and_audits_offline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = DatasetStore(tmp_path / "synthetic-cli.sqlite")
    route_synthetic(
        monkeypatch, EconomicPublisher.BEA, SyntheticResponse(synthetic_ics("20250310T123000Z"))
    )
    arguments = [
        "--db",
        str(store.path),
        "poll-economic-calendar",
        "bea",
        "--watch",
        "--max-attempts",
        "1",
    ]
    assert cli.main(arguments) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["attempted"] is True and status["state"] == "captured"
    command = [str(Path(executable).with_name("aurum")), "--db", str(store.path)]
    result = subprocess.run(
        [*command, "economic-pollers"], capture_output=True, text=True, check=True
    )
    assert json.loads(result.stdout)[0]["poller_id"] == status["poller_id"]
    audit = subprocess.run(
        [*command, "economic-poll-attempts", status["poller_id"]],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(audit.stdout)[0]["outcome"] == "captured"


@pytest.mark.parametrize(
    "mutation",
    (
        "UPDATE economic_pollers SET consecutive_failures = 1",
        "UPDATE economic_poll_attempts SET completed_at = '2025-03-10T12:01:00+00:00'",
        "UPDATE economic_poll_attempts SET capture_id = NULL",
    ),
)
def test_verified_backup_rejects_changed_poll_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    store = DatasetStore(tmp_path / "synthetic-corrupt-poll.sqlite")
    reference = register_economic_poller(store, config(), now=EARLY)
    route_synthetic(
        monkeypatch, EconomicPublisher.BEA, SyntheticResponse(synthetic_ics("20250310T123000Z"))
    )
    poll_economic_calendar(store, reference, clock=lambda: EARLY)
    target = tmp_path / "synthetic-good-backup.sqlite"
    backup_store(store, target, verify_normalized=True)
    with sqlite3.connect(store.path) as db:
        db.execute(mutation)
    rejected = tmp_path / "synthetic-rejected-backup.sqlite"
    with pytest.raises(ValueError, match="failed journal audit"):
        backup_store(store, rejected, verify_normalized=True)
    assert not rejected.exists()
