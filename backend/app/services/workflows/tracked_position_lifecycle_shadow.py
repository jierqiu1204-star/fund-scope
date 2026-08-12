from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.market_data import DECISION_ELIGIBLE_RELIABILITIES
from app.defaults.short_research import ASSET_TYPE_ETF
from app.models.entities import TrackedPosition
from app.services.risk_alerts import (
    evaluate_exit_execution_evidence,
    evaluate_position_risk_rule_set,
    legacy_exit_target_remaining_fraction,
)
from app.services.tracked_positions.jobs import (
    PositionEvaluationObserver,
    daily_tracked_position_alerts_job,
)
from app.services.tracked_positions.lifecycle import stable_contract_hash
from app.services.tracked_positions.lifecycle_rollout import (
    LifecycleRolloutMode,
    LifecycleRolloutPolicy,
)
from app.services.tracked_positions.service import PreparedAlertEvaluation
from app.services.workflows.tracked_position_lifecycle import (
    LifecycleEvaluationCommand,
    LifecycleEvaluationConflictError,
    execute_position_lifecycle_evaluation,
)

V2_SHADOW_POLICY_VERSION = "etf_exit_action_v3_shadow_v1"
V2_SHADOW_SNAPSHOT_SCHEMA = "etf_position_shadow_snapshot_v1"
MAX_SHADOW_POSITIONS_PER_RUN = 100


@dataclass(frozen=True)
class LifecycleShadowObservation:
    status: str
    reason_code: str
    outcome: str | None = None


class TrackedPositionLifecycleShadowObserver:
    """Bounded best-effort V2 observer; legacy output remains authoritative."""

    def __init__(
        self,
        *,
        enabled: bool,
        session_factory: Callable[[], AsyncSession] | None,
        limit: int = MAX_SHADOW_POSITIONS_PER_RUN,
    ) -> None:
        if not 1 <= limit <= MAX_SHADOW_POSITIONS_PER_RUN:
            raise ValueError("shadow position limit must be between 1 and 100")
        self._enabled = enabled
        self._session_factory = session_factory
        self._limit = limit
        self._claimed = 0

    async def observe(
        self,
        *,
        position: TrackedPosition,
        prepared: PreparedAlertEvaluation,
        evaluation_mode: str,
    ) -> LifecycleShadowObservation:
        if not self._enabled:
            return LifecycleShadowObservation("deferred", "shadow_disabled")
        if self._session_factory is None:
            return LifecycleShadowObservation("deferred", "session_factory_unavailable")
        if position.asset_type != ASSET_TYPE_ETF:
            return LifecycleShadowObservation("not_applicable", "non_etf_position")
        if self._claimed >= self._limit:
            return LifecycleShadowObservation("deferred", "run_position_bound_reached")
        self._claimed += 1

        state_reason = _position_state_reason(position)
        if state_reason is not None:
            return LifecycleShadowObservation("deferred", state_reason)
        if prepared.analysis is None and prepared.signal_date is None:
            return LifecycleShadowObservation("deferred", "market_snapshot_unavailable")

        command, data_eligible = _build_shadow_command(
            position=position,
            prepared=prepared,
            evaluation_mode=evaluation_mode,
        )
        policy = LifecycleRolloutPolicy.for_mode(LifecycleRolloutMode.SHADOW)
        if (
            policy.production_state_writes_enabled
            or policy.v2_action_writes_enabled
            or policy.notification_generation_enabled
        ):
            return LifecycleShadowObservation("failed", "unsafe_shadow_policy")

        try:
            result = await execute_position_lifecycle_evaluation(
                self._session_factory,
                command,
                rollout_policy=policy,
            )
        except LifecycleEvaluationConflictError:
            return LifecycleShadowObservation("deferred", "shadow_write_conflict")
        except ValueError as exc:
            message = str(exc).lower()
            if "out of order" in message:
                reason = "out_of_order_snapshot"
            elif "predecessor mismatch" in message:
                reason = "shadow_predecessor_mismatch"
            elif "baseline" in message or "exposure" in message or "episode" in message:
                reason = "lifecycle_state_unavailable"
            else:
                reason = "invalid_shadow_input"
            return LifecycleShadowObservation("deferred", reason)
        except Exception:  # noqa: BLE001 - shadow must not break the legacy job
            return LifecycleShadowObservation("failed", "shadow_evaluation_failed")

        status = (
            "duplicate"
            if result.outcome == "duplicate"
            else ("evaluated" if data_eligible else "data_ineligible")
        )
        return LifecycleShadowObservation(status, result.outcome, result.outcome)


def empty_shadow_counters() -> dict[str, int]:
    return {
        "shadow_evaluated": 0,
        "shadow_data_ineligible": 0,
        "shadow_failed": 0,
        "shadow_deferred": 0,
        "shadow_duplicates": 0,
    }


def add_shadow_observation(
    counters: dict[str, Any],
    observation: LifecycleShadowObservation,
) -> None:
    if observation.status == "evaluated":
        counters["shadow_evaluated"] += 1
    elif observation.status == "data_ineligible":
        counters["shadow_data_ineligible"] += 1
    elif observation.status == "duplicate":
        counters["shadow_evaluated"] += 1
        counters["shadow_duplicates"] += 1
    elif observation.status == "failed":
        counters["shadow_failed"] += 1
    elif observation.status == "deferred":
        counters["shadow_deferred"] += 1


def build_lifecycle_shadow_observer(
    *,
    settings: Settings,
    session_factory: Callable[[], AsyncSession],
) -> PositionEvaluationObserver:
    observer = TrackedPositionLifecycleShadowObserver(
        enabled=settings.tracked_position_lifecycle_shadow_enabled,
        session_factory=session_factory,
    )

    async def observe(
        position: TrackedPosition,
        prepared: PreparedAlertEvaluation,
        evaluation_mode: str,
    ) -> dict[str, int]:
        counters = empty_shadow_counters()
        add_shadow_observation(
            counters,
            await observer.observe(
                position=position,
                prepared=prepared,
                evaluation_mode=evaluation_mode,
            ),
        )
        return counters

    return observe


async def daily_tracked_position_alerts_with_shadow_job(
    session: AsyncSession,
    *,
    settings: Settings,
    session_factory: Callable[[], AsyncSession],
) -> dict[str, Any]:
    return await daily_tracked_position_alerts_job(
        session,
        settings,
        post_evaluation_observer=build_lifecycle_shadow_observer(
            settings=settings,
            session_factory=session_factory,
        ),
    )


def _position_state_reason(position: TrackedPosition) -> str | None:
    state = dict(position.exit_state_json or {})
    episode_id = str(state.get("position_episode_id") or "").strip()
    exposure_version = state.get("exposure_version")
    baseline = state.get("exposure_baseline")
    if not episode_id or not isinstance(exposure_version, int) or exposure_version < 1:
        return "missing_position_episode"
    if not isinstance(baseline, dict):
        return "missing_exposure_baseline"
    normalized_quantity = baseline.get("normalized_quantity")
    if (
        isinstance(normalized_quantity, bool)
        or not isinstance(normalized_quantity, (int, float))
        or not math.isfinite(float(normalized_quantity))
    ):
        return "invalid_exposure_baseline"
    raw_quantity = (
        position.confirmed_shares
        if position.confirmed_shares is not None
        else position.estimated_shares
    )
    if (
        isinstance(raw_quantity, bool)
        or not isinstance(raw_quantity, (int, float))
        or not math.isfinite(float(raw_quantity))
    ):
        return "position_quantity_unavailable"
    return None


def _build_shadow_command(
    *,
    position: TrackedPosition,
    prepared: PreparedAlertEvaluation,
    evaluation_mode: str,
) -> tuple[LifecycleEvaluationCommand, bool]:
    analysis = prepared.analysis
    snapshot = analysis.intraday_snapshot if analysis is not None else None
    execution = evaluate_exit_execution_evidence(
        signal_price=snapshot.current_price if snapshot is not None else None,
        bid_price=snapshot.bid_price if snapshot is not None else None,
        ask_price=snapshot.ask_price if snapshot is not None else None,
        entry_price=position.entry_price,
        hard_stop_pct=(
            analysis.technical_metrics.get("hard_stop_pct") if analysis is not None else None
        ),
        quote_eligible=bool(
            snapshot is not None
            and snapshot.price_source == "intraday_quote"
            and snapshot.reliability_level in DECISION_ELIGIBLE_RELIABILITIES
            and not snapshot.is_stale
            and snapshot.email_eligible
            and snapshot.decision_eligible
        ),
    )
    execution_data_eligible = execution.status == "observable"
    ma5_data_eligible = bool(
        analysis is not None
        and analysis.technical_metrics.get("ma5_close_break_decision_eligible") is True
    )
    any_data_eligible = execution_data_eligible or ma5_data_eligible
    if execution_data_eligible:
        data_reason_code = "fresh_explicit_intraday_quote"
    elif snapshot is None:
        data_reason_code = prepared.data_reason_code or "snapshot_unavailable"
    elif not snapshot.decision_eligible:
        data_reason_code = "quote_not_decision_eligible"
    else:
        data_reason_code = execution.reason_code or "execution_quote_unavailable"
    legacy_alert_type = prepared.decision.alert_type if prepared.decision is not None else None
    evaluations = evaluate_position_risk_rule_set(
        technical_metrics=(analysis.technical_metrics if analysis is not None else None),
        legacy_alert_type=legacy_alert_type,
        data_eligible=execution_data_eligible,
        data_reason_code=str(data_reason_code),
    )
    trade_session, observed_at = _market_identity_time(prepared)
    repeat_slot = f"{evaluation_mode}:{observed_at.isoformat(timespec='seconds')}"
    snapshot_payload = {
        "schema": V2_SHADOW_SNAPSHOT_SCHEMA,
        "asset_code": position.asset_code,
        "evaluation_mode": evaluation_mode,
        "trade_session": trade_session,
        "observed_at": observed_at,
        "price_source": snapshot.price_source if snapshot is not None else "unavailable",
        "quote_time": snapshot.quote_time if snapshot is not None else None,
        "trade_date": snapshot.trade_date if snapshot is not None else None,
        "current_price": snapshot.current_price if snapshot is not None else None,
        "bid_price": snapshot.bid_price if snapshot is not None else None,
        "ask_price": snapshot.ask_price if snapshot is not None else None,
        "decision_eligible": snapshot.decision_eligible if snapshot is not None else False,
        "chart_tail": [
            {"date": point.date, "price": point.price}
            for point in (analysis.chart[-20:] if analysis is not None else [])
        ],
        "legacy_alert_type": legacy_alert_type,
        "legacy_trigger_label": (
            prepared.decision.trigger_label if prepared.decision is not None else None
        ),
        "risk_flags": sorted(prepared.decision.risk_flags if prepared.decision is not None else []),
        "ma5_close_break": (
            analysis.technical_metrics.get("ma5_close_break") if analysis is not None else None
        ),
    }
    return (
        LifecycleEvaluationCommand(
            owner_id=position.user_id,
            position_id=position.id,
            expected_position_state_version=position.exit_state_version,
            policy_version=V2_SHADOW_POLICY_VERSION,
            sealed_snapshot_hash=stable_contract_hash(snapshot_payload),
            trade_session=trade_session,
            repeat_slot=repeat_slot,
            recipient="shadow-only",
            evaluations=evaluations,
            occurred_at=observed_at,
            eligible_data_time=observed_at,
            cutoff_time=observed_at,
            data_source=(
                snapshot.source or snapshot.price_source if snapshot is not None else "unavailable"
            ),
            quote_freshness=(snapshot.freshness_status if snapshot is not None else "unavailable"),
            legacy_comparison_status="compared",
            legacy_alert_type=legacy_alert_type,
            legacy_target_remaining_fraction=legacy_exit_target_remaining_fraction(
                legacy_alert_type
            ),
        ),
        any_data_eligible,
    )


def _market_identity_time(
    prepared: PreparedAlertEvaluation,
) -> tuple[date, datetime]:
    analysis = prepared.analysis
    snapshot = analysis.intraday_snapshot if analysis is not None else None
    if snapshot is not None and snapshot.quote_time is not None:
        observed_at = snapshot.quote_time
        if observed_at.tzinfo is not None:
            observed_at = observed_at.astimezone(UTC).replace(tzinfo=None)
        return snapshot.trade_date or observed_at.date(), observed_at
    if snapshot is not None and snapshot.trade_date is not None:
        return snapshot.trade_date, datetime.combine(snapshot.trade_date, time(15, 0))
    if analysis is not None and analysis.chart:
        chart_date = analysis.chart[-1].date
        return chart_date, datetime.combine(chart_date, time(15, 0))
    signal_date = prepared.signal_date
    if signal_date is None:
        raise ValueError("market snapshot date is unavailable")
    return signal_date, datetime.combine(signal_date, time(15, 0))
