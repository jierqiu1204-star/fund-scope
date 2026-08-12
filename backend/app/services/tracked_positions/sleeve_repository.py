from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    TrackedEtfSleeveDailySnapshot,
    TrackedEtfSleeveLedgerEvent,
    TrackedPosition,
    User,
)

LEDGER_CONTRACT_VERSION = "tracked_etf_sleeve_ledger_v1"
SNAPSHOT_CONTRACT_VERSION = "tracked_etf_sleeve_daily_snapshot_v1"
MAX_OWNER_LEDGER_READ = 1000
MAX_OWNER_SNAPSHOT_READ = 500

LEDGER_EVENT_TYPES = frozenset(
    {
        "opening_reconciliation",
        "reconciliation",
        "cash_deposit",
        "cash_withdrawal",
        "buy",
        "sell",
        "distribution",
        "fee",
        "quantity_adjustment",
    }
)
LEDGER_PROVENANCE = frozenset(
    {"owner_confirmed", "owner_documented", "broker_verified", "provider_verified"}
)
OWNER_ACCOUNT_EVENT_TYPES = frozenset(
    {
        "opening_reconciliation",
        "reconciliation",
        "cash_deposit",
        "cash_withdrawal",
        "buy",
        "sell",
        "fee",
    }
)
OWNER_ACCOUNT_PROVENANCE = frozenset({"owner_confirmed", "owner_documented", "broker_verified"})
class SleeveLedgerConflictError(RuntimeError):
    pass


class SleeveRepositoryValidationError(ValueError):
    pass


@dataclass(frozen=True)
class AppendSleeveLedgerEventCommand:
    owner_id: int
    idempotency_key: str
    event_type: str
    effective_date: date
    occurred_at: datetime
    provenance: str
    expected_predecessor_event_hash: str | None
    tracked_position_id: int | None = None
    asset_code: str | None = None
    cash_delta: float | None = None
    cash_balance_after: float | None = None
    quantity_delta: float | None = None
    quantity_after: float | None = None
    execution_price: float | None = None
    fees: float = 0.0
    adjustment_factor: float = 1.0
    holdings_after: tuple[Mapping[str, Any], ...] = ()
    evidence_ref: str | None = None
    reason_code: str | None = None
    contract_version: str = LEDGER_CONTRACT_VERSION


@dataclass(frozen=True)
class PersistedSleeveLedgerEvent:
    event: TrackedEtfSleeveLedgerEvent
    created: bool


@dataclass(frozen=True)
class AppendSleeveDailySnapshotCommand:
    owner_id: int
    idempotency_key: str
    snapshot_date: date
    cutoff_at: datetime
    coverage_state: str
    risk_state: str
    expected_predecessor_snapshot_hash: str | None
    ledger_head_event_hash: str | None
    input_contract_hash: str
    nav_contract_hash: str
    risk_policy_hash: str
    cash_balance: float | None = None
    market_value: float | None = None
    equity: float | None = None
    net_external_flow: float = 0.0
    flow_adjusted_nav: float | None = None
    high_water_nav: float | None = None
    drawdown_pct: float | None = None
    holding_count: int = 0
    valued_holding_count: int = 0
    ledger_coverage_ratio: float = 0.0
    valuation_coverage_ratio: float = 0.0
    recovery_streak: int = 0
    cooldown_sessions_remaining: int = 0
    signal_stop_cycle_count: int = 0
    confirmed_stop_cycle_count: int = 0
    reasons: tuple[str, ...] = ()
    contract_version: str = SNAPSHOT_CONTRACT_VERSION


@dataclass(frozen=True)
class PersistedSleeveDailySnapshot:
    snapshot: TrackedEtfSleeveDailySnapshot
    created: bool


def _normalized_scalar(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat(timespec="microseconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {
            str(key): _normalized_scalar(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_normalized_scalar(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise SleeveRepositoryValidationError("hash payload contains a non-finite number")
    return value


def _stable_hash(value: object) -> str:
    encoded = json.dumps(
        _normalized_scalar(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_owner_id(owner_id: int) -> None:
    if isinstance(owner_id, bool) or not isinstance(owner_id, int) or owner_id <= 0:
        raise SleeveRepositoryValidationError("owner_id must be a positive integer")


def _idempotency_key(value: str) -> str:
    normalized = value.strip() if isinstance(value, str) else ""
    if not normalized or len(normalized) > 128:
        raise SleeveRepositoryValidationError(
            "idempotency_key must contain between 1 and 128 characters"
        )
    return normalized


def _validate_optional_hash(name: str, value: str | None) -> None:
    if value is None:
        return
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise SleeveRepositoryValidationError(f"{name} must be a lowercase sha256 digest")


def _validate_hash(name: str, value: str) -> None:
    _validate_optional_hash(name, value)
    if value is None:  # pragma: no cover - guarded by the annotation, retained for callers
        raise SleeveRepositoryValidationError(f"{name} is required")


def _optional_finite(
    name: str,
    value: float | None,
    *,
    minimum: float | None = None,
    strict_minimum: bool = False,
) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise SleeveRepositoryValidationError(f"{name} must be finite")
    if minimum is not None:
        invalid = value <= minimum if strict_minimum else value < minimum
        if invalid:
            comparator = "greater than" if strict_minimum else "at least"
            raise SleeveRepositoryValidationError(f"{name} must be {comparator} {minimum}")


def _non_negative_int(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SleeveRepositoryValidationError(f"{name} must be a non-negative integer")


def _normalized_holdings(
    holdings: tuple[Mapping[str, Any], ...],
) -> tuple[dict[str, Any], ...]:
    normalized: list[dict[str, Any]] = []
    identities: set[tuple[int | None, str]] = set()
    for raw in holdings:
        if not isinstance(raw, Mapping):
            raise SleeveRepositoryValidationError("each reconciled holding must be a mapping")
        asset_code = str(raw.get("asset_code") or "").strip()
        if not asset_code or len(asset_code) > 32:
            raise SleeveRepositoryValidationError("reconciled holding asset_code is invalid")
        quantity = raw.get("quantity")
        remaining_cost_basis = raw.get("remaining_cost_basis")
        adjustment_factor = raw.get("adjustment_factor", 1.0)
        _optional_finite("reconciled holding quantity", quantity, minimum=0.0)
        _optional_finite(
            "reconciled holding remaining_cost_basis",
            remaining_cost_basis,
            minimum=0.0,
        )
        _optional_finite(
            "reconciled holding adjustment_factor",
            adjustment_factor,
            minimum=0.0,
            strict_minimum=True,
        )
        if quantity is None:
            raise SleeveRepositoryValidationError("reconciled holding quantity is required")
        if remaining_cost_basis is None:
            raise SleeveRepositoryValidationError(
                "reconciled holding remaining_cost_basis is required"
            )
        tracked_position_id = raw.get("tracked_position_id")
        if tracked_position_id is not None and (
            isinstance(tracked_position_id, bool)
            or not isinstance(tracked_position_id, int)
            or tracked_position_id <= 0
        ):
            raise SleeveRepositoryValidationError(
                "reconciled holding tracked_position_id must be a positive integer"
            )
        identity = (tracked_position_id, asset_code)
        if identity in identities:
            raise SleeveRepositoryValidationError("reconciled holding identity is duplicated")
        identities.add(identity)
        position_episode_id = raw.get("position_episode_id")
        normalized.append(
            {
                "tracked_position_id": tracked_position_id,
                "position_episode_id": (
                    None
                    if position_episode_id is None
                    else str(position_episode_id).strip() or None
                ),
                "asset_code": asset_code,
                "quantity": float(quantity),
                "remaining_cost_basis": float(remaining_cost_basis),
                "adjustment_factor": float(adjustment_factor),
            }
        )
    return tuple(
        sorted(
            normalized,
            key=lambda item: (
                item["asset_code"],
                item["tracked_position_id"] or 0,
                item["position_episode_id"] or "",
            ),
        )
    )


def _validate_text(name: str, value: str | None, *, max_length: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SleeveRepositoryValidationError(f"{name} must be a string")
    normalized = value.strip()
    if not normalized or len(normalized) > max_length:
        raise SleeveRepositoryValidationError(
            f"{name} must contain between 1 and {max_length} characters"
        )
    return normalized


def _validate_ledger_command(
    command: AppendSleeveLedgerEventCommand,
) -> tuple[str, tuple[dict[str, Any], ...], str | None, str | None]:
    _validate_owner_id(command.owner_id)
    key = _idempotency_key(command.idempotency_key)
    if command.event_type not in LEDGER_EVENT_TYPES:
        raise SleeveRepositoryValidationError("unsupported sleeve ledger event_type")
    if command.provenance not in LEDGER_PROVENANCE:
        raise SleeveRepositoryValidationError("unsupported sleeve ledger provenance")
    if not isinstance(command.effective_date, date) or isinstance(command.effective_date, datetime):
        raise SleeveRepositoryValidationError("effective_date must be a date")
    if not isinstance(command.occurred_at, datetime):
        raise SleeveRepositoryValidationError("occurred_at must be a datetime")
    if not command.contract_version.strip() or len(command.contract_version) > 64:
        raise SleeveRepositoryValidationError("contract_version is invalid")
    _validate_optional_hash(
        "expected_predecessor_event_hash",
        command.expected_predecessor_event_hash,
    )
    if command.tracked_position_id is not None and (
        isinstance(command.tracked_position_id, bool)
        or not isinstance(command.tracked_position_id, int)
        or command.tracked_position_id <= 0
    ):
        raise SleeveRepositoryValidationError("tracked_position_id must be a positive integer")
    asset_code = _validate_text("asset_code", command.asset_code, max_length=32)
    evidence_ref = _validate_text("evidence_ref", command.evidence_ref, max_length=255)
    reason_code = _validate_text("reason_code", command.reason_code, max_length=128)
    if (
        command.event_type in OWNER_ACCOUNT_EVENT_TYPES
        and command.provenance not in OWNER_ACCOUNT_PROVENANCE
    ):
        raise SleeveRepositoryValidationError(
            "owner account changes require owner-confirmed, documented or broker provenance"
        )
    if command.provenance != "owner_confirmed" and evidence_ref is None:
        raise SleeveRepositoryValidationError(
            "documented, broker and provider provenance require evidence_ref"
        )
    _optional_finite("cash_delta", command.cash_delta)
    _optional_finite("cash_balance_after", command.cash_balance_after, minimum=0.0)
    _optional_finite("quantity_delta", command.quantity_delta)
    _optional_finite("quantity_after", command.quantity_after, minimum=0.0)
    _optional_finite(
        "execution_price",
        command.execution_price,
        minimum=0.0,
        strict_minimum=True,
    )
    _optional_finite("fees", command.fees, minimum=0.0)
    _optional_finite(
        "adjustment_factor",
        command.adjustment_factor,
        minimum=0.0,
        strict_minimum=True,
    )
    holdings = _normalized_holdings(command.holdings_after)

    if command.event_type in {"opening_reconciliation", "reconciliation"}:
        if command.cash_balance_after is None:
            raise SleeveRepositoryValidationError(
                "reconciliation requires an explicit cash_balance_after"
            )
        if any(
            value is not None
            for value in (
                command.cash_delta,
                command.quantity_delta,
                command.quantity_after,
                command.execution_price,
            )
        ):
            raise SleeveRepositoryValidationError(
                "reconciliation must use cash_balance_after and holdings_after, not deltas"
            )
    elif holdings:
        raise SleeveRepositoryValidationError(
            "holdings_after is only valid for a reconciliation event"
        )

    if command.event_type in {"buy", "sell"}:
        if command.tracked_position_id is None or asset_code is None:
            raise SleeveRepositoryValidationError("trade requires a position and asset_code")
        if any(
            value is None
            for value in (
                command.quantity_delta,
                command.quantity_after,
                command.cash_delta,
                command.execution_price,
            )
        ):
            raise SleeveRepositoryValidationError(
                "trade requires quantity_delta, quantity_after, cash_delta and execution_price"
            )
        assert command.quantity_delta is not None
        assert command.cash_delta is not None
        assert command.execution_price is not None
        if command.event_type == "buy":
            if command.quantity_delta <= 0 or command.cash_delta >= 0:
                raise SleeveRepositoryValidationError(
                    "buy requires positive quantity_delta and negative net cash_delta"
                )
            expected_cash = -(command.quantity_delta * command.execution_price + command.fees)
        else:
            if command.quantity_delta >= 0 or command.cash_delta <= 0:
                raise SleeveRepositoryValidationError(
                    "sell requires negative quantity_delta and positive net cash_delta"
                )
            expected_cash = -command.quantity_delta * command.execution_price - command.fees
        tolerance = max(0.01, abs(expected_cash) * 1e-8)
        if not math.isclose(command.cash_delta, expected_cash, rel_tol=0.0, abs_tol=tolerance):
            raise SleeveRepositoryValidationError(
                "trade cash_delta is inconsistent with quantity, price and fees"
            )
    elif command.event_type == "cash_deposit" and not (
        command.cash_delta is not None and command.cash_delta > 0
    ):
        raise SleeveRepositoryValidationError("cash_deposit requires positive cash_delta")
    elif command.event_type == "cash_withdrawal" and not (
        command.cash_delta is not None and command.cash_delta < 0
    ):
        raise SleeveRepositoryValidationError("cash_withdrawal requires negative cash_delta")
    elif command.event_type == "distribution" and not (
        asset_code is not None and command.cash_delta is not None and command.cash_delta > 0
    ):
        raise SleeveRepositoryValidationError(
            "distribution requires asset_code and positive cash_delta"
        )
    elif command.event_type == "fee":
        if command.cash_delta is None or command.cash_delta >= 0 or command.fees <= 0:
            raise SleeveRepositoryValidationError(
                "fee requires negative cash_delta and positive fees"
            )
        if not math.isclose(
            command.cash_delta,
            -command.fees,
            rel_tol=0.0,
            abs_tol=max(0.01, command.fees * 1e-8),
        ):
            raise SleeveRepositoryValidationError("fee cash_delta must equal negative fees")
    elif command.event_type == "quantity_adjustment":
        if (
            command.tracked_position_id is None
            or asset_code is None
            or command.quantity_delta is None
            or command.quantity_delta == 0
            or command.quantity_after is None
        ):
            raise SleeveRepositoryValidationError(
                "quantity_adjustment requires position, asset and explicit quantity evidence"
            )
    return key, holdings, evidence_ref, reason_code


def _ledger_request_payload(
    command: AppendSleeveLedgerEventCommand,
    *,
    idempotency_key: str,
    holdings: tuple[dict[str, Any], ...],
    evidence_ref: str | None,
    reason_code: str | None,
) -> dict[str, object]:
    return {
        "contract_version": command.contract_version.strip(),
        "owner_id": command.owner_id,
        "idempotency_key": idempotency_key,
        "event_type": command.event_type,
        "effective_date": command.effective_date,
        "occurred_at": command.occurred_at,
        "provenance": command.provenance,
        "expected_predecessor_event_hash": command.expected_predecessor_event_hash,
        "tracked_position_id": command.tracked_position_id,
        "asset_code": None if command.asset_code is None else command.asset_code.strip(),
        "cash_delta": command.cash_delta,
        "cash_balance_after": command.cash_balance_after,
        "quantity_delta": command.quantity_delta,
        "quantity_after": command.quantity_after,
        "execution_price": command.execution_price,
        "fees": command.fees,
        "adjustment_factor": command.adjustment_factor,
        "holdings_after": holdings,
        "evidence_ref": evidence_ref,
        "reason_code": reason_code,
    }


async def _lock_owner(session: AsyncSession, owner_id: int) -> datetime | None:
    locked_owner = (
        await session.execute(
            select(User.id, User.etf_trading_capital_confirmed_at)
            .where(User.id == owner_id)
            .with_for_update()
        )
    ).one_or_none()
    if locked_owner is None:
        raise LookupError("owner not found")
    return locked_owner.etf_trading_capital_confirmed_at


async def _validate_owner_position_references(
    session: AsyncSession,
    *,
    owner_id: int,
    tracked_position_id: int | None,
    asset_code: str | None,
    holdings: tuple[dict[str, Any], ...],
) -> None:
    references: dict[int, str | None] = {}
    if tracked_position_id is not None:
        references[tracked_position_id] = asset_code
    for holding in holdings:
        holding_position_id = holding["tracked_position_id"]
        if holding_position_id is None:
            continue
        holding_asset_code = holding["asset_code"]
        prior_code = references.get(holding_position_id)
        if prior_code is not None and prior_code != holding_asset_code:
            raise SleeveRepositoryValidationError(
                "one tracked_position_id cannot reference multiple asset codes"
            )
        references[holding_position_id] = holding_asset_code
    if not references:
        return

    rows = (
        await session.execute(
            select(
                TrackedPosition.id,
                TrackedPosition.asset_type,
                TrackedPosition.asset_code,
            ).where(
                TrackedPosition.user_id == owner_id,
                TrackedPosition.id.in_(tuple(references)),
            )
        )
    ).all()
    positions = {row.id: (row.asset_type, row.asset_code) for row in rows}
    if set(positions) != set(references):
        raise SleeveRepositoryValidationError(
            "tracked position does not exist or belongs to another owner"
        )
    for position_id, expected_asset_code in references.items():
        asset_type, actual_asset_code = positions[position_id]
        if asset_type != "etf":
            raise SleeveRepositoryValidationError(
                "tracked ETF sleeve cannot reference a non-ETF position"
            )
        if expected_asset_code is not None and actual_asset_code != expected_asset_code:
            raise SleeveRepositoryValidationError(
                "tracked position asset_code does not match the ledger evidence"
            )


async def latest_owner_ledger_event(
    session: AsyncSession,
    *,
    owner_id: int,
) -> TrackedEtfSleeveLedgerEvent | None:
    _validate_owner_id(owner_id)
    return await session.scalar(
        select(TrackedEtfSleeveLedgerEvent)
        .where(TrackedEtfSleeveLedgerEvent.user_id == owner_id)
        .order_by(TrackedEtfSleeveLedgerEvent.sequence_no.desc())
        .limit(1)
    )


async def owner_ledger_events(
    session: AsyncSession,
    *,
    owner_id: int,
    after_sequence_no: int = 0,
    limit: int = 200,
) -> tuple[TrackedEtfSleeveLedgerEvent, ...]:
    _validate_owner_id(owner_id)
    _non_negative_int("after_sequence_no", after_sequence_no)
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= MAX_OWNER_LEDGER_READ
    ):
        raise SleeveRepositoryValidationError(
            f"limit must be between 1 and {MAX_OWNER_LEDGER_READ}"
        )
    rows = (
        await session.scalars(
            select(TrackedEtfSleeveLedgerEvent)
            .where(
                TrackedEtfSleeveLedgerEvent.user_id == owner_id,
                TrackedEtfSleeveLedgerEvent.sequence_no > after_sequence_no,
            )
            .order_by(TrackedEtfSleeveLedgerEvent.sequence_no.asc())
            .limit(limit)
        )
    ).all()
    return tuple(rows)


async def append_owner_ledger_event(
    session: AsyncSession,
    command: AppendSleeveLedgerEventCommand,
) -> PersistedSleeveLedgerEvent:
    key, holdings, evidence_ref, reason_code = _validate_ledger_command(command)
    request_hash = _stable_hash(
        _ledger_request_payload(
            command,
            idempotency_key=key,
            holdings=holdings,
            evidence_ref=evidence_ref,
            reason_code=reason_code,
        )
    )
    existing = await session.scalar(
        select(TrackedEtfSleeveLedgerEvent).where(
            TrackedEtfSleeveLedgerEvent.user_id == command.owner_id,
            TrackedEtfSleeveLedgerEvent.idempotency_key == key,
        )
    )
    if existing is not None:
        if existing.request_hash != request_hash:
            raise SleeveLedgerConflictError(
                "idempotency_key was already used with a different ledger payload"
            )
        return PersistedSleeveLedgerEvent(existing, created=False)

    capital_confirmed_at = await _lock_owner(session, command.owner_id)
    if capital_confirmed_at is None:
        raise SleeveRepositoryValidationError(
            "ETF trading capital must be owner-confirmed before ledger events are appended"
        )
    existing = await session.scalar(
        select(TrackedEtfSleeveLedgerEvent).where(
            TrackedEtfSleeveLedgerEvent.user_id == command.owner_id,
            TrackedEtfSleeveLedgerEvent.idempotency_key == key,
        )
    )
    if existing is not None:
        if existing.request_hash != request_hash:
            raise SleeveLedgerConflictError(
                "idempotency_key was already used with a different ledger payload"
            )
        return PersistedSleeveLedgerEvent(existing, created=False)

    asset_code = None if command.asset_code is None else command.asset_code.strip()
    await _validate_owner_position_references(
        session,
        owner_id=command.owner_id,
        tracked_position_id=command.tracked_position_id,
        asset_code=asset_code,
        holdings=holdings,
    )

    latest = await latest_owner_ledger_event(session, owner_id=command.owner_id)
    predecessor_hash = latest.event_hash if latest is not None else None
    if command.expected_predecessor_event_hash != predecessor_hash:
        raise SleeveLedgerConflictError("ledger predecessor does not match the current owner head")
    if latest is None and command.event_type != "opening_reconciliation":
        raise SleeveRepositoryValidationError(
            "the first owner ledger event must be opening_reconciliation"
        )
    if latest is not None and command.event_type == "opening_reconciliation":
        raise SleeveRepositoryValidationError(
            "opening_reconciliation is valid only as the first owner ledger event"
        )

    sequence_no = 1 if latest is None else latest.sequence_no + 1
    event_hash = _stable_hash(
        {
            "contract_version": command.contract_version.strip(),
            "owner_id": command.owner_id,
            "sequence_no": sequence_no,
            "predecessor_event_hash": predecessor_hash,
            "request_hash": request_hash,
        }
    )
    row = TrackedEtfSleeveLedgerEvent(
        user_id=command.owner_id,
        tracked_position_id=command.tracked_position_id,
        sequence_no=sequence_no,
        idempotency_key=key,
        request_hash=request_hash,
        event_type=command.event_type,
        asset_code=asset_code,
        effective_date=command.effective_date,
        occurred_at=command.occurred_at,
        cash_delta=command.cash_delta,
        cash_balance_after=command.cash_balance_after,
        quantity_delta=command.quantity_delta,
        quantity_after=command.quantity_after,
        execution_price=command.execution_price,
        fees=float(command.fees),
        adjustment_factor=float(command.adjustment_factor),
        holdings_after_json=list(holdings),
        provenance=command.provenance,
        evidence_ref=evidence_ref,
        reason_code=reason_code,
        contract_version=command.contract_version.strip(),
        predecessor_event_hash=predecessor_hash,
        event_hash=event_hash,
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError as exc:
        existing = await session.scalar(
            select(TrackedEtfSleeveLedgerEvent).where(
                TrackedEtfSleeveLedgerEvent.user_id == command.owner_id,
                TrackedEtfSleeveLedgerEvent.idempotency_key == key,
            )
        )
        if existing is not None and existing.request_hash == request_hash:
            return PersistedSleeveLedgerEvent(existing, created=False)
        raise SleeveLedgerConflictError("concurrent ledger append changed the owner head") from exc
    return PersistedSleeveLedgerEvent(row, created=True)


def _normalized_reasons(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(sorted({str(value).strip() for value in values if str(value).strip()}))
    if any(len(value) > 128 for value in normalized):
        raise SleeveRepositoryValidationError("snapshot reason exceeds 128 characters")
    return normalized


def _validate_snapshot_command(
    command: AppendSleeveDailySnapshotCommand,
) -> tuple[str, tuple[str, ...]]:
    _validate_owner_id(command.owner_id)
    key = _idempotency_key(command.idempotency_key)
    if not isinstance(command.snapshot_date, date) or isinstance(command.snapshot_date, datetime):
        raise SleeveRepositoryValidationError("snapshot_date must be a date")
    if not isinstance(command.cutoff_at, datetime):
        raise SleeveRepositoryValidationError("cutoff_at must be a datetime")
    if command.coverage_state not in {"eligible", "unavailable"}:
        raise SleeveRepositoryValidationError("unsupported coverage_state")
    if command.risk_state not in {"normal", "reduce_only", "data_halt"}:
        raise SleeveRepositoryValidationError("unsupported risk_state")
    _validate_optional_hash(
        "expected_predecessor_snapshot_hash",
        command.expected_predecessor_snapshot_hash,
    )
    _validate_optional_hash("ledger_head_event_hash", command.ledger_head_event_hash)
    _validate_hash("input_contract_hash", command.input_contract_hash)
    _validate_hash("nav_contract_hash", command.nav_contract_hash)
    _validate_hash("risk_policy_hash", command.risk_policy_hash)
    if not command.contract_version.strip() or len(command.contract_version) > 64:
        raise SleeveRepositoryValidationError("contract_version is invalid")
    for name, value, minimum, strict in (
        ("cash_balance", command.cash_balance, 0.0, False),
        ("market_value", command.market_value, 0.0, False),
        ("equity", command.equity, 0.0, False),
        ("net_external_flow", command.net_external_flow, None, False),
        ("flow_adjusted_nav", command.flow_adjusted_nav, 0.0, True),
        ("high_water_nav", command.high_water_nav, 0.0, True),
        # Despite the historical column suffix, drawdown is stored as a
        # decimal fraction (for example -0.12 means -12%).
        ("drawdown_pct", command.drawdown_pct, -1.0, False),
        ("ledger_coverage_ratio", command.ledger_coverage_ratio, 0.0, False),
        ("valuation_coverage_ratio", command.valuation_coverage_ratio, 0.0, False),
    ):
        _optional_finite(name, value, minimum=minimum, strict_minimum=strict)
    if command.drawdown_pct is not None and command.drawdown_pct > 0:
        raise SleeveRepositoryValidationError("drawdown_pct must not be positive")
    if not 0 <= command.ledger_coverage_ratio <= 1:
        raise SleeveRepositoryValidationError("ledger_coverage_ratio must be between 0 and 1")
    if not 0 <= command.valuation_coverage_ratio <= 1:
        raise SleeveRepositoryValidationError("valuation_coverage_ratio must be between 0 and 1")
    for name, value in (
        ("holding_count", command.holding_count),
        ("valued_holding_count", command.valued_holding_count),
        ("recovery_streak", command.recovery_streak),
        ("cooldown_sessions_remaining", command.cooldown_sessions_remaining),
        ("signal_stop_cycle_count", command.signal_stop_cycle_count),
        ("confirmed_stop_cycle_count", command.confirmed_stop_cycle_count),
    ):
        _non_negative_int(name, value)
    if command.valued_holding_count > command.holding_count:
        raise SleeveRepositoryValidationError("valued_holding_count exceeds holding_count")
    if command.confirmed_stop_cycle_count > command.signal_stop_cycle_count:
        raise SleeveRepositoryValidationError(
            "confirmed stop cycles exceed observed signal stop cycles"
        )
    reasons = _normalized_reasons(command.reasons)

    if command.coverage_state == "eligible":
        required = (
            command.cash_balance,
            command.market_value,
            command.equity,
            command.flow_adjusted_nav,
            command.high_water_nav,
            command.ledger_head_event_hash,
        )
        if any(value is None for value in required):
            raise SleeveRepositoryValidationError(
                "eligible snapshot requires complete cash, valuation, NAV and ledger evidence"
            )
        if command.ledger_coverage_ratio != 1 or command.valuation_coverage_ratio != 1:
            raise SleeveRepositoryValidationError(
                "eligible snapshot requires complete ledger and valuation coverage"
            )
        if command.valued_holding_count != command.holding_count:
            raise SleeveRepositoryValidationError(
                "eligible snapshot requires every holding to be valued"
            )
        assert command.cash_balance is not None
        assert command.market_value is not None
        assert command.equity is not None
        assert command.flow_adjusted_nav is not None
        assert command.high_water_nav is not None
        equity_tolerance = max(0.01, command.equity * 1e-8)
        if not math.isclose(
            command.equity,
            command.cash_balance + command.market_value,
            rel_tol=0.0,
            abs_tol=equity_tolerance,
        ):
            raise SleeveRepositoryValidationError("equity must equal cash plus market value")
        if command.high_water_nav + 1e-12 < command.flow_adjusted_nav:
            raise SleeveRepositoryValidationError("high_water_nav is below flow_adjusted_nav")
        if command.risk_state != "data_halt":
            if command.drawdown_pct is None:
                raise SleeveRepositoryValidationError(
                    "eligible normal/reduce_only snapshot requires drawdown evidence"
                )
            expected_drawdown = command.flow_adjusted_nav / command.high_water_nav - 1.0
            if not math.isclose(
                command.drawdown_pct,
                expected_drawdown,
                rel_tol=0.0,
                abs_tol=1e-8,
            ):
                raise SleeveRepositoryValidationError(
                    "drawdown_pct is inconsistent with flow_adjusted_nav and high_water_nav"
                )
    else:
        if command.risk_state != "data_halt":
            raise SleeveRepositoryValidationError(
                "unavailable snapshot must persist data_halt risk_state"
            )
        if any(
            value is not None
            for value in (
                command.equity,
                command.flow_adjusted_nav,
                command.high_water_nav,
                command.drawdown_pct,
            )
        ):
            raise SleeveRepositoryValidationError(
                "unavailable snapshot cannot publish equity, NAV, high-water or drawdown"
            )
        if not reasons:
            raise SleeveRepositoryValidationError("unavailable snapshot requires a stable reason")
    return key, reasons


def _snapshot_request_payload(
    command: AppendSleeveDailySnapshotCommand,
    *,
    idempotency_key: str,
    reasons: tuple[str, ...],
) -> dict[str, object]:
    return {
        "contract_version": command.contract_version.strip(),
        "owner_id": command.owner_id,
        "idempotency_key": idempotency_key,
        "snapshot_date": command.snapshot_date,
        "cutoff_at": command.cutoff_at,
        "coverage_state": command.coverage_state,
        "risk_state": command.risk_state,
        "expected_predecessor_snapshot_hash": command.expected_predecessor_snapshot_hash,
        "ledger_head_event_hash": command.ledger_head_event_hash,
        "input_contract_hash": command.input_contract_hash,
        "nav_contract_hash": command.nav_contract_hash,
        "risk_policy_hash": command.risk_policy_hash,
        "cash_balance": command.cash_balance,
        "market_value": command.market_value,
        "equity": command.equity,
        "net_external_flow": command.net_external_flow,
        "flow_adjusted_nav": command.flow_adjusted_nav,
        "high_water_nav": command.high_water_nav,
        "drawdown_pct": command.drawdown_pct,
        "holding_count": command.holding_count,
        "valued_holding_count": command.valued_holding_count,
        "ledger_coverage_ratio": command.ledger_coverage_ratio,
        "valuation_coverage_ratio": command.valuation_coverage_ratio,
        "recovery_streak": command.recovery_streak,
        "cooldown_sessions_remaining": command.cooldown_sessions_remaining,
        "signal_stop_cycle_count": command.signal_stop_cycle_count,
        "confirmed_stop_cycle_count": command.confirmed_stop_cycle_count,
        "reasons": reasons,
    }


async def latest_owner_daily_snapshot(
    session: AsyncSession,
    *,
    owner_id: int,
) -> TrackedEtfSleeveDailySnapshot | None:
    _validate_owner_id(owner_id)
    return await session.scalar(
        select(TrackedEtfSleeveDailySnapshot)
        .where(TrackedEtfSleeveDailySnapshot.user_id == owner_id)
        .order_by(TrackedEtfSleeveDailySnapshot.sequence_no.desc())
        .limit(1)
    )


async def owner_daily_snapshots(
    session: AsyncSession,
    *,
    owner_id: int,
    before_sequence_no: int | None = None,
    limit: int = 100,
) -> tuple[TrackedEtfSleeveDailySnapshot, ...]:
    _validate_owner_id(owner_id)
    if before_sequence_no is not None:
        _non_negative_int("before_sequence_no", before_sequence_no)
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= MAX_OWNER_SNAPSHOT_READ
    ):
        raise SleeveRepositoryValidationError(
            f"limit must be between 1 and {MAX_OWNER_SNAPSHOT_READ}"
        )
    query = select(TrackedEtfSleeveDailySnapshot).where(
        TrackedEtfSleeveDailySnapshot.user_id == owner_id
    )
    if before_sequence_no is not None:
        query = query.where(TrackedEtfSleeveDailySnapshot.sequence_no < before_sequence_no)
    rows = (
        await session.scalars(
            query.order_by(TrackedEtfSleeveDailySnapshot.sequence_no.desc()).limit(limit)
        )
    ).all()
    return tuple(rows)


async def append_owner_daily_snapshot(
    session: AsyncSession,
    command: AppendSleeveDailySnapshotCommand,
) -> PersistedSleeveDailySnapshot:
    key, reasons = _validate_snapshot_command(command)
    request_hash = _stable_hash(
        _snapshot_request_payload(command, idempotency_key=key, reasons=reasons)
    )
    existing = await session.scalar(
        select(TrackedEtfSleeveDailySnapshot).where(
            TrackedEtfSleeveDailySnapshot.user_id == command.owner_id,
            TrackedEtfSleeveDailySnapshot.idempotency_key == key,
        )
    )
    if existing is not None:
        if existing.request_hash != request_hash:
            raise SleeveLedgerConflictError(
                "idempotency_key was already used with a different snapshot payload"
            )
        return PersistedSleeveDailySnapshot(existing, created=False)

    capital_confirmed_at = await _lock_owner(session, command.owner_id)
    existing = await session.scalar(
        select(TrackedEtfSleeveDailySnapshot).where(
            TrackedEtfSleeveDailySnapshot.user_id == command.owner_id,
            TrackedEtfSleeveDailySnapshot.idempotency_key == key,
        )
    )
    if existing is not None:
        if existing.request_hash != request_hash:
            raise SleeveLedgerConflictError(
                "idempotency_key was already used with a different snapshot payload"
            )
        return PersistedSleeveDailySnapshot(existing, created=False)
    if command.coverage_state == "eligible" and capital_confirmed_at is None:
        raise SleeveRepositoryValidationError(
            "eligible sleeve NAV requires owner-confirmed ETF trading capital"
        )
    latest_ledger = await latest_owner_ledger_event(session, owner_id=command.owner_id)
    current_ledger_hash = latest_ledger.event_hash if latest_ledger is not None else None
    if command.ledger_head_event_hash != current_ledger_hash:
        raise SleeveLedgerConflictError(
            "snapshot ledger head does not match the current owner ledger"
        )
    latest_snapshot = await latest_owner_daily_snapshot(session, owner_id=command.owner_id)
    predecessor_hash = latest_snapshot.snapshot_hash if latest_snapshot is not None else None
    if command.expected_predecessor_snapshot_hash != predecessor_hash:
        raise SleeveLedgerConflictError(
            "snapshot predecessor does not match the current owner snapshot head"
        )
    sequence_no = 1 if latest_snapshot is None else latest_snapshot.sequence_no + 1
    snapshot_hash = _stable_hash(
        {
            "contract_version": command.contract_version.strip(),
            "owner_id": command.owner_id,
            "sequence_no": sequence_no,
            "predecessor_snapshot_hash": predecessor_hash,
            "request_hash": request_hash,
        }
    )
    row = TrackedEtfSleeveDailySnapshot(
        user_id=command.owner_id,
        sequence_no=sequence_no,
        snapshot_date=command.snapshot_date,
        cutoff_at=command.cutoff_at,
        idempotency_key=key,
        request_hash=request_hash,
        cash_balance=command.cash_balance,
        market_value=command.market_value,
        equity=command.equity,
        net_external_flow=float(command.net_external_flow),
        flow_adjusted_nav=command.flow_adjusted_nav,
        high_water_nav=command.high_water_nav,
        drawdown_pct=command.drawdown_pct,
        holding_count=command.holding_count,
        valued_holding_count=command.valued_holding_count,
        ledger_coverage_ratio=float(command.ledger_coverage_ratio),
        valuation_coverage_ratio=float(command.valuation_coverage_ratio),
        coverage_state=command.coverage_state,
        risk_state=command.risk_state,
        recovery_streak=command.recovery_streak,
        cooldown_sessions_remaining=command.cooldown_sessions_remaining,
        signal_stop_cycle_count=command.signal_stop_cycle_count,
        confirmed_stop_cycle_count=command.confirmed_stop_cycle_count,
        reasons_json=list(reasons),
        ledger_head_event_hash=command.ledger_head_event_hash,
        input_contract_hash=command.input_contract_hash,
        nav_contract_hash=command.nav_contract_hash,
        risk_policy_hash=command.risk_policy_hash,
        contract_version=command.contract_version.strip(),
        predecessor_snapshot_hash=predecessor_hash,
        snapshot_hash=snapshot_hash,
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError as exc:
        existing = await session.scalar(
            select(TrackedEtfSleeveDailySnapshot).where(
                TrackedEtfSleeveDailySnapshot.user_id == command.owner_id,
                TrackedEtfSleeveDailySnapshot.idempotency_key == key,
            )
        )
        if existing is not None and existing.request_hash == request_hash:
            return PersistedSleeveDailySnapshot(existing, created=False)
        raise SleeveLedgerConflictError(
            "concurrent snapshot append changed the owner head"
        ) from exc
    return PersistedSleeveDailySnapshot(row, created=True)
