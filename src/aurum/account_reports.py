import json
from dataclasses import asdict
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path

from aurum.account_simulation import (
    ACCOUNT_VERSION,
    AccountMark,
    AccountPhase,
    AccountRules,
    AccountSimulator,
    AccountStatus,
)
from aurum.exchange_calendar import SessionCalendar
from aurum.storage import DatasetStore


def simulate_account_ledger(
    store: DatasetStore,
    ledger_path: Path,
    calendar: SessionCalendar,
    phase: AccountPhase,
    rules: AccountRules,
) -> str:
    return simulate_account_document(store, ledger_path.read_bytes(), calendar, phase, rules)


def simulate_account_document(
    store: DatasetStore,
    raw: bytes,
    calendar: SessionCalendar,
    phase: AccountPhase,
    rules: AccountRules,
) -> str:
    document = json.loads(raw)
    if (
        not isinstance(document, dict)
        or document.keys() != {"data_kind", "source", "started_at", "actions"}
        or document["data_kind"] not in ("synthetic", "paper_marks")
        or not isinstance(document["source"], str)
        or not document["source"].strip()
        or not isinstance(document["actions"], list)
    ):
        raise ValueError("ledger requires data_kind, source, started_at and actions")
    started_at = _timestamp(document["started_at"])
    simulator = AccountSimulator(calendar, started_at, phase, rules)
    state = simulator.snapshot(started_at)
    timeline = []
    skipped = 0
    observed = 0
    previous_at = started_at
    for action in document["actions"]:
        if not isinstance(action, dict):
            raise ValueError("account action must be an object")
        at = _timestamp(action.get("at"))
        if at < previous_at:
            raise ValueError("ledger actions must be chronological")
        previous_at = at
        kind = action.get("kind")
        if kind == "mark":
            mark = _mark(action, at)
            if simulator.status is not AccountStatus.ACTIVE:
                skipped += 1
                continue
            state = simulator.observe(mark)
            observed += 1
            entry = {"kind": kind, "state": state}
        elif kind == "payout" and action.keys() == {"kind", "at", "gross_cents"}:
            payout = simulator.request_payout(at, _integer(action["gross_cents"]))
            state = simulator.snapshot(at)
            entry = {"kind": kind, "payout": payout.as_dict(), "state": state}
        elif kind == "activate_funded" and action.keys() == {"kind", "at"}:
            state = simulator.activate_funded(at)
            entry = {"kind": kind, "state": state}
        else:
            raise ValueError(f"invalid account action: {kind!r}")
        timeline.append(entry)

    definition = {
        "account_version": ACCOUNT_VERSION,
        "initial_phase": phase,
        "rules": asdict(rules),
        "calendar": calendar.as_dict(),
        "ledger_sha256": sha256(raw).hexdigest(),
        "ledger": document,
    }
    return store.save_analysis(
        "paper_account",
        definition,
        {
            "execution_mode": "paper_research",
            "data_kind": document["data_kind"],
            "source": document["source"],
            "status": simulator.status,
            "final_state": state,
            "observed_marks": observed,
            "measurement": "explicit_observed_marks_only",
            "skipped_inactive_marks": skipped,
            "timeline": timeline,
        },
    )


def _mark(action: dict[str, object], at: datetime) -> AccountMark:
    if (
        not {"kind", "at", "trade_date"}
        <= action.keys()
        <= {
            "kind",
            "at",
            "trade_date",
            "closed_trade_nets_cents",
            "unrealized_cents",
            "open_micros",
            "end_of_day",
        }
    ):
        raise ValueError("invalid account mark fields")
    trade_date = action["trade_date"]
    closed = action.get("closed_trade_nets_cents", [])
    end_of_day = action.get("end_of_day", False)
    if (
        not isinstance(trade_date, str)
        or not isinstance(closed, list)
        or not isinstance(end_of_day, bool)
    ):
        raise ValueError("invalid account mark date, trade list or EOD flag")
    day = date.fromisoformat(trade_date)
    if day.isoformat() != trade_date:
        raise ValueError("account trade date must use YYYY-MM-DD")
    return AccountMark(
        at,
        day,
        tuple(_integer(item) for item in closed),
        _integer(action.get("unrealized_cents", 0)),
        _integer(action.get("open_micros", 0)),
        end_of_day,
    )


def _integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("account amounts and quantities must be integers")
    return value


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str) or "T" not in value:
        raise ValueError("account timestamps require an ISO date and time")
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        raise ValueError("account timestamps must include a UTC offset")
    return stamp.astimezone(UTC)
