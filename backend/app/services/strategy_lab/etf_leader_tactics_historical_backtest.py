"""Bounded, research-only rolling backtest for ETF leader proxy candidates.

The evaluator intentionally uses current-vintage membership and taxonomy. Price
features and outcomes are date-local and total-return-adjusted, but the result is
not point-in-time membership evidence and therefore receives zero promotion
credit.
"""

from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date
from statistics import mean, median
from typing import Any

from app.services.etf_research_evidence import stable_contract_hash
from app.services.strategy_lab.etf_factor_evidence import (
    FactorEvidencePayload,
    FactorEvidencePromotion,
)
from app.services.strategy_lab.etf_leader_entry_quality import (
    ENTRY_QUALITY_CONTRACT_VERSION,
    assess_leader_breakout_entry_quality,
    assess_leader_next_session_confirmation,
)
from app.services.strategy_lab.etf_leader_tactics_shadow import (
    BREAKOUT_HISTORY_SESSIONS,
    FORMER_LEADER_REPAIR_CANDIDATE,
    FROZEN_LEADER_CANDIDATE_REGISTRY,
    LEADER_BREAKOUT_CANDIDATE,
    LEADER_HYPOTHESIS_REGISTRY,
    MINIMUM_PEER_COUNT,
    REPAIR_HISTORY_SESSIONS,
)

LEADER_HISTORICAL_BACKTEST_EXPERIMENT_FAMILY = (
    "leader_tactics_historical_backtest_v1"
)
LEADER_HISTORICAL_BACKTEST_SCHEMA_VERSION = (
    "etf_leader_tactics_historical_backtest_evidence_v1"
)
LEADER_HISTORICAL_BACKTEST_REPORT_KIND = "historical_proxy_rolling_backtest"
LEADER_HISTORICAL_BACKTEST_EVIDENCE_MODE = (
    "current_vintage_membership_rolling_research_replay"
)
LEADER_HISTORICAL_BACKTEST_UNAVAILABLE = (
    "leader_historical_backtest_not_materialized"
)
LEADER_HISTORICAL_BACKTEST_INCOMPATIBLE = (
    "leader_historical_backtest_incompatible"
)
LEADER_HISTORICAL_BACKTEST_NOT_PIT = (
    "historical_backtest_membership_not_point_in_time"
)

HISTORICAL_BACKTEST_HORIZONS = (1, 3, 5, 10, 20)
ONE_WAY_FEE_BPS = 5
ONE_WAY_SLIPPAGE_BPS = 5
ROUND_TRIP_COST_RATE = 2 * (ONE_WAY_FEE_BPS + ONE_WAY_SLIPPAGE_BPS) / 10_000
HISTORICAL_SECTOR_PROXY_VERSION = "sector_trend_historical_neutral_technical_v1"
HISTORICAL_BACKTEST_CONTRACT = {
    "schema_version": LEADER_HISTORICAL_BACKTEST_SCHEMA_VERSION,
    "candidate_registry_hash": FROZEN_LEADER_CANDIDATE_REGISTRY.registry_hash,
    "candidate_ids": (
        LEADER_BREAKOUT_CANDIDATE,
        FORMER_LEADER_REPAIR_CANDIDATE,
    ),
    "membership_mode": "sealed_source_snapshot_current_vintage_proxy",
    "price_basis": "total_return_adjusted",
    "signal_timing": "signal_at_T_close_enter_at_T_plus_1_adjusted_close",
    "horizons": HISTORICAL_BACKTEST_HORIZONS,
    "one_way_fee_bps": ONE_WAY_FEE_BPS,
    "one_way_slippage_bps": ONE_WAY_SLIPPAGE_BPS,
    "sector_proxy_version": HISTORICAL_SECTOR_PROXY_VERSION,
    "peer_benchmark": "same_signal_date_equal_weight_current_vintage_peer_group",
    "clone_policy": "highest_average_turnover20_then_asset_code",
    "entry_quality_diagnostic": ENTRY_QUALITY_CONTRACT_VERSION,
    "next_session_confirmation": "daily_close_proxy",
    "confirmation_entry_timing": (
        "confirm_at_T_plus_1_close_enter_at_T_plus_2_adjusted_open"
    ),
    "production_mutation_allowed": False,
}
HISTORICAL_BACKTEST_CONTRACT_HASH = stable_contract_hash(
    HISTORICAL_BACKTEST_CONTRACT
)


class LeaderHistoricalBacktestContractError(ValueError):
    """Raised when a historical proxy backtest violates its frozen contract."""


@dataclass(frozen=True)
class HistoricalLeaderBar:
    trade_date: date
    adjusted_open: float
    adjusted_high: float
    adjusted_low: float
    adjusted_close: float
    volume: float
    turnover: float


@dataclass(frozen=True)
class HistoricalLeaderAsset:
    asset_code: str
    name: str | None
    peer_group: str
    clone_group: str
    baseline_score: float | None
    bars: tuple[HistoricalLeaderBar, ...]


@dataclass(frozen=True)
class HistoricalLeaderEvent:
    signal_date: date
    candidate_id: str
    asset_code: str
    name: str | None
    peer_group: str
    score: float
    feature_hash: str
    entry_date: date
    exit_date: date
    horizon_sessions: int
    gross_return: float
    net_return: float
    peer_net_return: float
    net_excess_return: float
    entry_quality_state: str | None
    entry_quality_reason_codes: tuple[str, ...]
    next_session_confirmation_state: str | None
    next_session_confirmation_reason_codes: tuple[str, ...]
    confirmation_date: date | None
    confirmed_entry_date: date | None
    confirmed_exit_date: date | None
    confirmed_net_return: float | None
    confirmed_peer_net_return: float | None
    confirmed_net_excess_return: float | None

    def payload(self) -> dict[str, Any]:
        return asdict(self)


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) else None


def _percentiles(
    values: Mapping[str, float], *, reverse: bool = False
) -> dict[str, float]:
    finite = {
        key: parsed
        for key, value in values.items()
        if (parsed := _finite(value)) is not None
    }
    ordered = sorted(finite.items(), key=lambda item: (item[1], item[0]))
    if not ordered:
        return {}
    denominator = max(1, len(ordered) - 1)
    result: dict[str, float] = {}
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][1] == ordered[index][1]:
            end += 1
        rank = ((index + end - 1) / 2) / denominator if len(ordered) > 1 else 0.5
        if reverse:
            rank = 1.0 - rank
        for position in range(index, end):
            result[ordered[position][0]] = rank
        index = end
    return result


def _atr(bars: Sequence[HistoricalLeaderBar], sessions: int) -> float | None:
    if len(bars) < sessions + 1:
        return None
    window = bars[-(sessions + 1) :]
    values = [
        max(
            current.adjusted_high - current.adjusted_low,
            abs(current.adjusted_high - previous.adjusted_close),
            abs(current.adjusted_low - previous.adjusted_close),
        )
        for previous, current in zip(window[:-1], window[1:], strict=True)
    ]
    result = mean(values)
    return result if math.isfinite(result) and result > 0 else None


def _validate_assets(assets: Sequence[HistoricalLeaderAsset]) -> None:
    FROZEN_LEADER_CANDIDATE_REGISTRY.validate()
    if HISTORICAL_BACKTEST_CONTRACT["candidate_registry_hash"] != (
        FROZEN_LEADER_CANDIDATE_REGISTRY.registry_hash
    ):
        raise LeaderHistoricalBacktestContractError(
            "historical backtest candidate registry drifted"
        )
    if len({item.asset_code for item in assets}) != len(assets):
        raise LeaderHistoricalBacktestContractError("asset codes must be unique")
    for asset in assets:
        if not asset.asset_code.strip() or not asset.peer_group.strip():
            raise LeaderHistoricalBacktestContractError(
                "asset identity and current-vintage peer group are required"
            )
        dates = tuple(bar.trade_date for bar in asset.bars)
        if dates != tuple(sorted(set(dates))):
            raise LeaderHistoricalBacktestContractError(
                f"adjusted history is not canonical for {asset.asset_code}"
            )
        for bar in asset.bars:
            values = (
                bar.adjusted_open,
                bar.adjusted_high,
                bar.adjusted_low,
                bar.adjusted_close,
                bar.volume,
                bar.turnover,
            )
            if any(_finite(value) is None for value in values):
                raise LeaderHistoricalBacktestContractError(
                    f"non-finite adjusted history for {asset.asset_code}"
                )
            if (
                bar.adjusted_open <= 0
                or bar.adjusted_high <= 0
                or bar.adjusted_low <= 0
                or bar.adjusted_close <= 0
                or bar.volume < 0
                or bar.turnover < 0
                or bar.adjusted_high
                < max(bar.adjusted_open, bar.adjusted_close)
                or bar.adjusted_low > min(bar.adjusted_open, bar.adjusted_close)
            ):
                raise LeaderHistoricalBacktestContractError(
                    f"invalid adjusted OHLCV for {asset.asset_code}"
                )


def eligible_historical_signal_dates(
    assets: Sequence[HistoricalLeaderAsset],
) -> tuple[date, ...]:
    """Return dates with enough trailing data and complete 20-session outcomes."""

    _validate_assets(assets)
    counts: dict[date, int] = defaultdict(int)
    for asset in assets:
        for index in range(
            REPAIR_HISTORY_SESSIONS - 1,
            len(asset.bars) - max(HISTORICAL_BACKTEST_HORIZONS) - 1,
        ):
            counts[asset.bars[index].trade_date] += 1
    return tuple(
        signal_date
        for signal_date, count in sorted(counts.items())
        if count >= MINIMUM_PEER_COUNT
    )


def _feature_rows(
    assets: Sequence[HistoricalLeaderAsset], signal_date: date
) -> tuple[list[dict[str, Any]], dict[str, HistoricalLeaderAsset], dict[str, int]]:
    features: list[dict[str, Any]] = []
    by_code: dict[str, HistoricalLeaderAsset] = {}
    indices: dict[str, int] = {}
    for asset in assets:
        index_by_date = {bar.trade_date: index for index, bar in enumerate(asset.bars)}
        index = index_by_date.get(signal_date)
        if index is None or index < BREAKOUT_HISTORY_SESSIONS - 1:
            continue
        bars = asset.bars[: index + 1]
        closes = [bar.adjusted_close for bar in bars]
        return20 = closes[-1] / closes[-21] - 1.0
        return5 = closes[-1] / closes[-6] - 1.0
        turnover20 = mean(bar.turnover for bar in bars[-20:])
        turnover60 = (
            mean(bar.turnover for bar in bars[-60:]) if len(bars) >= 60 else None
        )
        ma5 = mean(closes[-5:])
        ma10 = mean(closes[-10:])
        ma20 = mean(closes[-20:])
        features.append(
            {
                "asset_code": asset.asset_code,
                "peer_group": asset.peer_group,
                "clone_group": asset.clone_group or asset.asset_code,
                "baseline_score": asset.baseline_score,
                "history_count": len(bars),
                "return5": return5,
                "return20": return20,
                "average_turnover20": turnover20,
                "turnover_ratio20_60": (
                    turnover20 / turnover60
                    if turnover60 is not None and turnover60 > 0
                    else 1.0
                ),
                "ma5": ma5,
                "ma10": ma10,
                "ma20": ma20,
                "close": closes[-1],
                "open": bars[-1].adjusted_open,
                "prior_close": closes[-2],
                "preceding_high20": max(
                    bar.adjusted_high for bar in bars[-21:-1]
                ),
                "current_volume": bars[-1].volume,
                "volume_max120": max(bar.volume for bar in bars[-120:]),
                "drawdown120": closes[-1] / max(closes[-120:]) - 1.0,
                "atr5": _atr(bars, 5),
                "atr20": _atr(bars, 20),
                "bars": bars,
            }
        )
        by_code[asset.asset_code] = asset
        indices[asset.asset_code] = index
    return features, by_code, indices


def _historical_sector_percentiles(
    groups: Mapping[str, Sequence[dict[str, Any]]]
) -> dict[str, float]:
    scores: dict[str, float] = {}
    for group, rows in groups.items():
        if len(rows) < MINIMUM_PEER_COUNT:
            continue
        count = len(rows)
        avg5 = mean(float(item["return5"]) for item in rows)
        avg20 = mean(float(item["return20"]) for item in rows)
        positive = sum(float(item["return5"]) > 0 for item in rows)
        ma5_positive = sum(float(item["close"]) >= float(item["ma5"]) for item in rows)
        ma20_positive = sum(float(item["close"]) >= float(item["ma20"]) for item in rows)
        avg_turnover_ratio = mean(float(item["turnover_ratio20_60"]) for item in rows)
        return_score = min(100.0, max(0.0, 50.0 + avg5 * 250.0 + avg20 * 125.0))
        breadth_score = (
            (positive / count + ma5_positive / count + ma20_positive / count)
            / 3
            * 100
        )
        turnover_score = min(90.0, max(0.0, 50.0 + (avg_turnover_ratio - 1.0) * 50.0))
        # Historical comprehensive technical scores are unavailable. A frozen
        # neutral 50 preserves date-locality and is explicit in the contract.
        scores[group] = (
            return_score * 0.35
            + breadth_score * 0.30
            + 50.0 * 0.20
            + turnover_score * 0.15
        )
    return _percentiles(scores)


def _prior_leadership(
    rows: Sequence[dict[str, Any]], groups: Mapping[str, Sequence[dict[str, Any]]]
) -> dict[str, float]:
    result: dict[str, float] = {}
    for group_rows in groups.values():
        eligible = [item for item in group_rows if item["history_count"] >= REPAIR_HISTORY_SESSIONS]
        if len(eligible) < MINIMUM_PEER_COUNT:
            continue
        by_date: dict[date, dict[str, float]] = defaultdict(dict)
        for item in eligible:
            bars = item["bars"]
            for index in range(len(bars) - 120, len(bars) - 20):
                if index < 20:
                    continue
                start = bars[index - 20].adjusted_close
                if start > 0:
                    by_date[bars[index].trade_date][item["asset_code"]] = (
                        bars[index].adjusted_close / start - 1.0
                    )
        for values in by_date.values():
            if len(values) < MINIMUM_PEER_COUNT:
                continue
            for code, percentile in _percentiles(values).items():
                result[code] = max(result.get(code, percentile), percentile)
    return result


def _select_candidates(
    features: Sequence[dict[str, Any]], by_code: Mapping[str, HistoricalLeaderAsset]
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in features:
        groups[str(item["peer_group"])].append(item)
    return_pct: dict[str, float] = {}
    turnover_pct: dict[str, float] = {}
    peer_count: dict[str, int] = {}
    for rows in groups.values():
        return_pct.update(
            _percentiles({str(item["asset_code"]): float(item["return20"]) for item in rows})
        )
        turnover_pct.update(
            _percentiles(
                {
                    str(item["asset_code"]): float(item["average_turnover20"])
                    for item in rows
                }
            )
        )
        for item in rows:
            peer_count[str(item["asset_code"])] = len(rows)
    sector_pct = _historical_sector_percentiles(groups)
    prior = _prior_leadership(features, groups)

    breakout: list[dict[str, Any]] = []
    repair_raw: dict[str, dict[str, float]] = {}
    for item in features:
        code = str(item["asset_code"])
        group_percentile = sector_pct.get(str(item["peer_group"]))
        if (
            peer_count.get(code, 0) >= MINIMUM_PEER_COUNT
            and item["ma5"] > item["ma10"] > item["ma20"]
            and item["close"] > item["preceding_high20"]
            and item["current_volume"] >= item["volume_max120"]
            and group_percentile is not None
            and group_percentile >= 2 / 3
            and return_pct.get(code, -1.0) >= 0.8
            and turnover_pct.get(code, -1.0) >= 0.5
        ):
            entry_quality = assess_leader_breakout_entry_quality(
                adjusted_close=item["close"],
                preceding_adjusted_high=item["preceding_high20"],
                adjusted_ma20=item["ma20"],
                adjusted_atr20=item["atr20"],
                volume_confirmed=item["current_volume"] >= item["volume_max120"],
            )
            components = {
                "sector_trend_percentile": group_percentile,
                "peer_return20_percentile": return_pct[code],
                "peer_turnover20_percentile": turnover_pct[code],
                "peer_count": peer_count[code],
                **entry_quality.component_payload(),
            }
            breakout.append(
                {
                    "candidate_id": LEADER_BREAKOUT_CANDIDATE,
                    "asset_code": code,
                    "score": mean(components[key] for key in (
                        "sector_trend_percentile",
                        "peer_return20_percentile",
                        "peer_turnover20_percentile",
                    )),
                    "average_turnover20": item["average_turnover20"],
                    "components": components,
                }
            )
        if item["history_count"] < REPAIR_HISTORY_SESSIONS:
            continue
        atr5 = item["atr5"]
        atr20 = item["atr20"]
        prior_value = prior.get(code)
        if atr5 is None or atr20 is None or prior_value is None:
            continue
        compression = atr5 / atr20
        overextension = abs(item["close"] - item["ma20"]) / atr20
        if (
            peer_count.get(code, 0) >= MINIMUM_PEER_COUNT
            and prior_value >= 0.8
            and -0.50 <= item["drawdown120"] <= -0.30
            and item["close"] > item["open"]
            and item["close"] > item["prior_close"]
            and compression <= 0.75
            and overextension <= 1.0
        ):
            repair_raw[code] = {
                "prior_leadership_percentile": prior_value,
                "atr5_atr20_ratio": compression,
                "overextension_atr": overextension,
                "average_turnover20": float(item["average_turnover20"]),
            }
    reverse_compression = _percentiles(
        {code: item["atr5_atr20_ratio"] for code, item in repair_raw.items()},
        reverse=True,
    )
    reverse_overextension = _percentiles(
        {code: item["overextension_atr"] for code, item in repair_raw.items()},
        reverse=True,
    )
    repair = []
    for code, item in repair_raw.items():
        components = {
            "prior_leadership_percentile": item["prior_leadership_percentile"],
            "reverse_atr5_atr20_percentile": reverse_compression[code],
            "reverse_overextension_atr_percentile": reverse_overextension[code],
        }
        repair.append(
            {
                "candidate_id": FORMER_LEADER_REPAIR_CANDIDATE,
                "asset_code": code,
                "score": mean(components.values()),
                "average_turnover20": item["average_turnover20"],
                "components": components,
            }
        )

    selected: list[dict[str, Any]] = []
    for candidate_id in (LEADER_BREAKOUT_CANDIDATE, FORMER_LEADER_REPAIR_CANDIDATE):
        clone_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in (*breakout, *repair):
            if row["candidate_id"] != candidate_id:
                continue
            asset = by_code[str(row["asset_code"])]
            clone_rows[asset.clone_group or asset.asset_code].append(row)
        for rows in clone_rows.values():
            selected.append(
                max(
                    rows,
                    key=lambda row: (
                        float(row["average_turnover20"]),
                        str(row["asset_code"]),
                    ),
                )
            )
    return sorted(
        selected,
        key=lambda row: (
            str(row["candidate_id"]),
            -float(row["score"]),
            str(row["asset_code"]),
        ),
    )


def evaluate_historical_signal_date(
    assets: Sequence[HistoricalLeaderAsset], signal_date: date
) -> tuple[HistoricalLeaderEvent, ...]:
    """Evaluate one date without using prices after T in candidate selection."""

    _validate_assets(assets)
    features, by_code, indices = _feature_rows(assets, signal_date)
    selected = _select_candidates(features, by_code)
    peer_forward: dict[tuple[str, int], float] = {}
    confirmed_peer_forward: dict[tuple[str, int], float] = {}
    for group in {asset.peer_group for asset in by_code.values()}:
        peers = [asset for asset in by_code.values() if asset.peer_group == group]
        for horizon in HISTORICAL_BACKTEST_HORIZONS:
            values: list[float] = []
            for asset in peers:
                index = indices[asset.asset_code]
                exit_index = index + 1 + horizon
                if exit_index >= len(asset.bars):
                    continue
                entry = asset.bars[index + 1].adjusted_close
                exit_price = asset.bars[exit_index].adjusted_close
                values.append(exit_price / entry - 1.0 - ROUND_TRIP_COST_RATE)
            if values:
                peer_forward[(group, horizon)] = mean(values)
            confirmed_values: list[float] = []
            for asset in peers:
                index = indices[asset.asset_code]
                exit_index = index + 1 + horizon
                if exit_index >= len(asset.bars):
                    continue
                entry = asset.bars[index + 2].adjusted_open
                exit_price = asset.bars[exit_index].adjusted_close
                confirmed_values.append(
                    exit_price / entry - 1.0 - ROUND_TRIP_COST_RATE
                )
            if confirmed_values:
                confirmed_peer_forward[(group, horizon)] = mean(confirmed_values)

    events: list[HistoricalLeaderEvent] = []
    for candidate in selected:
        code = str(candidate["asset_code"])
        asset = by_code[code]
        index = indices[code]
        feature_payload = {
            "contract_hash": HISTORICAL_BACKTEST_CONTRACT_HASH,
            "signal_date": signal_date,
            "candidate_id": candidate["candidate_id"],
            "asset_code": code,
            "score": candidate["score"],
            "components": candidate["components"],
        }
        feature_hash = stable_contract_hash(feature_payload)
        entry_quality_state = candidate["components"].get("entry_quality_state")
        confirmation = None
        if candidate["candidate_id"] == LEADER_BREAKOUT_CANDIDATE:
            confirmation_bars = asset.bars[: index + 2]
            confirmation_bar = confirmation_bars[-1]
            confirmation = assess_leader_next_session_confirmation(
                signal_quality_state=entry_quality_state,
                signal_adjusted_close=asset.bars[index].adjusted_close,
                signal_adjusted_high=asset.bars[index].adjusted_high,
                preceding_adjusted_high=max(
                    bar.adjusted_high for bar in asset.bars[index - 20 : index]
                ),
                confirmation_adjusted_close=confirmation_bar.adjusted_close,
                confirmation_adjusted_ma5=mean(
                    bar.adjusted_close for bar in confirmation_bars[-5:]
                ),
                confirmation_adjusted_ma20=mean(
                    bar.adjusted_close for bar in confirmation_bars[-20:]
                ),
                confirmation_adjusted_atr20=_atr(confirmation_bars, 20),
            )
        for horizon in HISTORICAL_BACKTEST_HORIZONS:
            exit_index = index + 1 + horizon
            peer_return = peer_forward.get((asset.peer_group, horizon))
            if exit_index >= len(asset.bars) or peer_return is None:
                continue
            entry_bar = asset.bars[index + 1]
            exit_bar = asset.bars[exit_index]
            gross_return = exit_bar.adjusted_close / entry_bar.adjusted_close - 1.0
            net_return = gross_return - ROUND_TRIP_COST_RATE
            confirmed_entry_index = index + 2
            confirmed_exit_index = confirmed_entry_index + horizon - 1
            confirmed_peer_return = confirmed_peer_forward.get(
                (asset.peer_group, horizon)
            )
            confirmed_net_return = None
            confirmed_entry_date = None
            confirmed_exit_date = None
            if (
                confirmation is not None
                and confirmation.state == "confirmed"
                and confirmed_exit_index < len(asset.bars)
            ):
                confirmed_entry = asset.bars[confirmed_entry_index]
                confirmed_exit = asset.bars[confirmed_exit_index]
                confirmed_entry_date = confirmed_entry.trade_date
                confirmed_exit_date = confirmed_exit.trade_date
                confirmed_net_return = (
                    confirmed_exit.adjusted_close / confirmed_entry.adjusted_open
                    - 1.0
                    - ROUND_TRIP_COST_RATE
                )
            events.append(
                HistoricalLeaderEvent(
                    signal_date=signal_date,
                    candidate_id=str(candidate["candidate_id"]),
                    asset_code=code,
                    name=asset.name,
                    peer_group=asset.peer_group,
                    score=float(candidate["score"]),
                    feature_hash=feature_hash,
                    entry_date=entry_bar.trade_date,
                    exit_date=exit_bar.trade_date,
                    horizon_sessions=horizon,
                    gross_return=gross_return,
                    net_return=net_return,
                    peer_net_return=peer_return,
                    net_excess_return=net_return - peer_return,
                    entry_quality_state=(
                        str(entry_quality_state) if entry_quality_state else None
                    ),
                    entry_quality_reason_codes=tuple(
                        filter(
                            None,
                            str(
                                candidate["components"].get(
                                    "entry_quality_reason_codes", ""
                                )
                            ).split(";"),
                        )
                    ),
                    next_session_confirmation_state=(
                        confirmation.state if confirmation is not None else None
                    ),
                    next_session_confirmation_reason_codes=(
                        confirmation.reason_codes if confirmation is not None else ()
                    ),
                    confirmation_date=(
                        asset.bars[index + 1].trade_date
                        if confirmation is not None
                        else None
                    ),
                    confirmed_entry_date=confirmed_entry_date,
                    confirmed_exit_date=confirmed_exit_date,
                    confirmed_net_return=confirmed_net_return,
                    confirmed_peer_net_return=(
                        confirmed_peer_return
                        if confirmed_net_return is not None
                        else None
                    ),
                    confirmed_net_excess_return=(
                        confirmed_net_return - confirmed_peer_return
                        if confirmed_net_return is not None
                        and confirmed_peer_return is not None
                        else None
                    ),
                )
            )
    return tuple(events)


def _event_series_drawdown(events: Sequence[HistoricalLeaderEvent]) -> float | None:
    by_date: dict[date, list[float]] = defaultdict(list)
    for event in events:
        by_date[event.signal_date].append(event.net_return)
    if not by_date:
        return None
    equity = 1.0
    peak = 1.0
    maximum = 0.0
    for values in (by_date[key] for key in sorted(by_date)):
        equity *= 1.0 + mean(values)
        peak = max(peak, equity)
        maximum = min(maximum, equity / peak - 1.0)
    return maximum


def _block_bootstrap_interval(
    values: Sequence[float], *, seed: int, block_size: int = 5, draws: int = 1_000
) -> tuple[float | None, float | None]:
    if len(values) < 2:
        return None, None
    rng = random.Random(seed)
    n = len(values)
    estimates: list[float] = []
    for _ in range(draws):
        sample: list[float] = []
        while len(sample) < n:
            start = rng.randrange(n)
            sample.extend(values[(start + offset) % n] for offset in range(block_size))
        estimates.append(mean(sample[:n]))
    estimates.sort()
    return estimates[int(draws * 0.025)], estimates[int(draws * 0.975)]


def _entry_quality_performance(
    events: Sequence[HistoricalLeaderEvent],
) -> dict[str, dict[str, float | int]]:
    groups: dict[str, list[HistoricalLeaderEvent]] = defaultdict(list)
    for event in events:
        groups[event.entry_quality_state or "not_applicable"].append(event)
    return {
        state: {
            "event_count": len(rows),
            "signal_date_count": len({event.signal_date for event in rows}),
            "mean_net_return": mean(event.net_return for event in rows),
            "win_rate": mean(event.net_return > 0 for event in rows),
            "mean_net_excess_return": mean(
                event.net_excess_return for event in rows
            ),
        }
        for state, rows in sorted(groups.items())
    }


def _confirmation_policy_performance(
    events: Sequence[HistoricalLeaderEvent],
) -> dict[str, float | int | None]:
    completed = [
        event for event in events if event.confirmed_net_return is not None
    ]
    return {
        "confirmed_event_count": len(completed),
        "confirmed_signal_date_count": len(
            {event.signal_date for event in completed}
        ),
        "mean_net_return": (
            mean(float(event.confirmed_net_return) for event in completed)
            if completed
            else None
        ),
        "win_rate": (
            mean(float(event.confirmed_net_return) > 0 for event in completed)
            if completed
            else None
        ),
        "mean_net_excess_return": (
            mean(
                float(event.confirmed_net_excess_return)
                for event in completed
                if event.confirmed_net_excess_return is not None
            )
            if any(
                event.confirmed_net_excess_return is not None
                for event in completed
            )
            else None
        ),
    }


def summarize_historical_events(
    events: Sequence[HistoricalLeaderEvent],
) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    candidate_ids = (
        "all_leader_candidates",
        LEADER_BREAKOUT_CANDIDATE,
        FORMER_LEADER_REPAIR_CANDIDATE,
    )
    for candidate_id in candidate_ids:
        source = (
            list(events)
            if candidate_id == "all_leader_candidates"
            else [event for event in events if event.candidate_id == candidate_id]
        )
        for horizon in HISTORICAL_BACKTEST_HORIZONS:
            subset = [event for event in source if event.horizon_sessions == horizon]
            by_date: dict[date, list[float]] = defaultdict(list)
            for event in subset:
                by_date[event.signal_date].append(event.net_return)
            date_values = [mean(by_date[key]) for key in sorted(by_date)]
            lower, upper = _block_bootstrap_interval(
                date_values,
                seed=int(
                    stable_contract_hash(
                        {"candidate_id": candidate_id, "horizon": horizon}
                    )[:8],
                    16,
                ),
            )
            rows.append(
                {
                    "candidate_id": candidate_id,
                    "horizon_sessions": horizon,
                    "event_count": len(subset),
                    "signal_date_count": len(by_date),
                    "mean_net_return": mean(event.net_return for event in subset)
                    if subset
                    else None,
                    "median_net_return": median(event.net_return for event in subset)
                    if subset
                    else None,
                    "win_rate": mean(event.net_return > 0 for event in subset)
                    if subset
                    else None,
                    "mean_peer_net_return": mean(
                        event.peer_net_return for event in subset
                    )
                    if subset
                    else None,
                    "mean_net_excess_return": mean(
                        event.net_excess_return for event in subset
                    )
                    if subset
                    else None,
                    "mean_net_return_ci95_lower": lower,
                    "mean_net_return_ci95_upper": upper,
                    "event_series_max_drawdown": _event_series_drawdown(subset),
                    "entry_quality_counts": dict(
                        sorted(
                            Counter(
                                event.entry_quality_state or "not_applicable"
                                for event in subset
                            ).items()
                        )
                    ),
                    "entry_quality_performance": _entry_quality_performance(subset),
                    "next_session_confirmation_counts": dict(
                        sorted(
                            Counter(
                                event.next_session_confirmation_state
                                or "not_applicable"
                                for event in subset
                            ).items()
                        )
                    ),
                    "confirmation_policy_performance": (
                        _confirmation_policy_performance(subset)
                    ),
                }
            )
    return tuple(rows)


def build_historical_backtest_evidence(
    report: Mapping[str, Any], *, code_version: str
) -> FactorEvidencePayload:
    """Validate and wrap a finalized rolling report as immutable research evidence."""

    required = {
        "schema_version": LEADER_HISTORICAL_BACKTEST_SCHEMA_VERSION,
        "report_kind": LEADER_HISTORICAL_BACKTEST_REPORT_KIND,
        "experiment_family": LEADER_HISTORICAL_BACKTEST_EXPERIMENT_FAMILY,
        "evidence_mode": LEADER_HISTORICAL_BACKTEST_EVIDENCE_MODE,
        "membership_mode": "sealed_source_snapshot_current_vintage_proxy",
        "price_basis": "total_return_adjusted",
        "status": "insufficient_data",
        "unavailable_reason": LEADER_HISTORICAL_BACKTEST_NOT_PIT,
        "contract_hash": HISTORICAL_BACKTEST_CONTRACT_HASH,
        "research_only": True,
        "production_mutation_allowed": False,
    }
    for key, expected in required.items():
        if report.get(key) != expected:
            raise LeaderHistoricalBacktestContractError(
                f"historical backtest report has incompatible {key}"
            )
    promotion_credit = report.get("promotion_gate_credit")
    if promotion_credit != {
        "eligible_pit_sessions": 0,
        "independent_primary_dates": 0,
        "walk_forward_folds": 0,
    }:
        raise LeaderHistoricalBacktestContractError(
            "historical backtest must have zero PIT promotion credit"
        )
    manifest_hash = stable_contract_hash(
        {key: value for key, value in report.items() if key != "manifest_hash"}
    )
    if report.get("manifest_hash") != manifest_hash:
        raise LeaderHistoricalBacktestContractError(
            "historical backtest manifest hash is incompatible"
        )
    limitations = tuple(str(item) for item in report.get("limitations", ()))
    aggregates = tuple(report.get("aggregates", ()))
    samples = tuple(dict(item) for item in report.get("sample_events", ()))
    return FactorEvidencePayload(
        manifest_hash=manifest_hash,
        ranking_contract_hash=str(report["source_ranking_contract_hash"]),
        code_version=code_version,
        samples=samples,
        aggregates={
            "status": "insufficient_data",
            "coverage": dict(report.get("coverage", {})),
            "aggregates": aggregates,
            "promotion_gate_credit": promotion_credit,
        },
        exclusions=tuple(
            {"reason": key, "count": value}
            for key, value in sorted(dict(report.get("exclusion_counts", {})).items())
        ),
        intervals={
            "multiplicity": {
                "method": "holm_bonferroni",
                "raw_primary_p_values": [1.0],
                "adjusted_primary_p_values": [1.0],
            },
            "interpretation": "descriptive_block_bootstrap_not_primary_inference",
        },
        split_reports={
            "historical_proxy_only": True,
            "holdout_consumed": False,
            "current_vintage_membership_bias": True,
        },
        costs={
            "one_way_fee_bps": ONE_WAY_FEE_BPS,
            "one_way_slippage_bps": ONE_WAY_SLIPPAGE_BPS,
            "round_trip_cost_rate": ROUND_TRIP_COST_RATE,
        },
        limitations=limitations,
        report=dict(report),
        promotion=FactorEvidencePromotion(
            state="insufficient_data",
            passed=False,
            failed_gates=(
                "historical_membership_not_point_in_time",
                "current_vintage_taxonomy_lookahead_bias",
            ),
            endpoint="none_historical_proxy_backtest",
        ),
        experiment_family=LEADER_HISTORICAL_BACKTEST_EXPERIMENT_FAMILY,
        hypothesis_registry_hash=LEADER_HYPOTHESIS_REGISTRY.registry_hash,
    )


def stable_event_payloads(
    events: Iterable[HistoricalLeaderEvent],
) -> tuple[dict[str, Any], ...]:
    return tuple(
        event.payload()
        for event in sorted(
            events,
            key=lambda item: (
                item.signal_date,
                item.candidate_id,
                item.asset_code,
                item.horizon_sessions,
            ),
        )
    )
