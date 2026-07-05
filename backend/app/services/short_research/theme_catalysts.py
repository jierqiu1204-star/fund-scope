from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import EtfThemeCatalystEvent, EtfThemeCatalystSnapshot, utcnow

CATALYST_SCORE_VERSION = "theme_catalyst_v1"
OPPORTUNITY_SCORE_VERSION = "opportunity_score_v2_catalyst_only"
OPPORTUNITY_SCORE_FULL_VERSION = "opportunity_score_v2_full"
OPPORTUNITY_SCORE_SECTOR_ONLY_VERSION = "opportunity_score_v2_sector_only"
OPPORTUNITY_SCORE_UNAVAILABLE_VERSION = "opportunity_score_v2_unavailable"
TECHNICAL_WEIGHT = 0.70
CATALYST_WEIGHT = 0.20
SENTIMENT_WEIGHT = 0.10
FULL_TECHNICAL_WEIGHT = 0.60
FULL_SECTOR_WEIGHT = 0.20
FULL_CATALYST_WEIGHT = 0.15
FULL_SENTIMENT_WEIGHT = 0.05
SECTOR_ONLY_TECHNICAL_WEIGHT = 0.75
SECTOR_ONLY_WEIGHT = 0.25
NEUTRAL_COMPONENT_SCORE = 50.0
RISK_ENTRY_LABELS = {"冲高别追", "跌破等待", "放量转弱"}
DATA_LIMITING_RISKS = {"数据不足", "数据滞后", "流动性不足"}


@dataclass(frozen=True)
class ThemeCatalystSeed:
    theme_key: str
    theme_name: str
    catalyst_type: str
    title: str
    summary: str
    source_url: str
    event_date: date
    effective_start: date
    effective_end: date
    strength_score: float
    confidence_score: float
    metadata: dict[str, Any]


THEME_CATALYST_SEEDS: tuple[ThemeCatalystSeed, ...] = (
    ThemeCatalystSeed(
        theme_key="机器人",
        theme_name="机器人/具身智能",
        catalyst_type="ipo",
        title="宇树科技 IPO 预期带动具身智能关注",
        summary="宇树科技 IPO 进程与具身智能产业热度提升，机器人 ETF 进入主题催化观察。",
        source_url="https://www.barrons.com/articles/tesla-stock-price-robots-unitree-5ab7e90e",
        event_date=date(2026, 7, 1),
        effective_start=date(2026, 7, 1),
        effective_end=date(2026, 12, 31),
        strength_score=92.0,
        confidence_score=78.0,
        metadata={"ai_boundary": "manual_seed", "watch_wording": "主题强但仍需等待买点确认"},
    ),
    ThemeCatalystSeed(
        theme_key="半导体",
        theme_name="半导体/芯片",
        catalyst_type="industry_policy",
        title="半导体国产替代和先进制造链持续催化",
        summary="国产替代、设备材料和先进封装预期支撑半导体主题关注。",
        source_url="https://www.gov.cn/zhengce/",
        event_date=date(2026, 7, 1),
        effective_start=date(2026, 7, 1),
        effective_end=date(2026, 12, 31),
        strength_score=82.0,
        confidence_score=72.0,
        metadata={"ai_boundary": "manual_seed"},
    ),
    ThemeCatalystSeed(
        theme_key="光模块",
        theme_name="光模块/CPO",
        catalyst_type="ai_infrastructure",
        title="AI 算力链带动光模块和 CPO 关注",
        summary="数据中心算力扩张、800G/1.6T 光模块和 CPO 方向构成光通信链条催化。",
        source_url="https://www.miit.gov.cn/",
        event_date=date(2026, 7, 1),
        effective_start=date(2026, 7, 1),
        effective_end=date(2026, 12, 31),
        strength_score=86.0,
        confidence_score=70.0,
        metadata={"ai_boundary": "manual_seed"},
    ),
    ThemeCatalystSeed(
        theme_key="光模块_proxy",
        theme_name="光模块/CPO 代理主题",
        catalyst_type="proxy_theme",
        title="暂无精确光模块 ETF，使用通信/5G/信息技术代理观察",
        summary="当没有精确光模块 ETF 时，只能用通信、5G 或信息技术类 ETF 作为代理主题观察。",
        source_url="https://www.csindex.com.cn/",
        event_date=date(2026, 7, 1),
        effective_start=date(2026, 7, 1),
        effective_end=date(2026, 12, 31),
        strength_score=72.0,
        confidence_score=58.0,
        metadata={"proxy_theme": True, "ai_boundary": "manual_seed"},
    ),
)


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return round(max(low, min(high, value)), 2)


def _event_date(event: Any) -> date | None:
    value = getattr(event, "event_date", None)
    return value if isinstance(value, date) else None


def _source_url(event: Any) -> str:
    return str(getattr(event, "source_url", "") or "").strip()


def _metadata(event: Any) -> Mapping[str, Any]:
    value = getattr(event, "metadata_json", None)
    return value if isinstance(value, Mapping) else {}


def _event_is_scoreable(event: Any, as_of_date: date) -> tuple[bool, str | None]:
    if str(getattr(event, "status", "") or "") != "active":
        return False, "事件尚未确认。"
    if not _source_url(event):
        return False, "事件缺少来源。"
    if _event_date(event) is None:
        return False, "事件缺少日期。"
    start = getattr(event, "effective_start", None)
    end = getattr(event, "effective_end", None)
    if isinstance(start, date) and as_of_date < start:
        return False, "事件尚未进入有效期。"
    if isinstance(end, date) and as_of_date > end:
        return False, "事件已过有效期。"
    return True, None


def _recency_decay(event: Any, as_of_date: date) -> float:
    event_date = _event_date(event)
    if event_date is None:
        return 0.0
    age_days = max((as_of_date - event_date).days, 0)
    if age_days <= 30:
        return 1.0
    return max(0.45, 1.0 - (age_days - 30) / 365)


def score_theme_catalysts(
    events: Sequence[Any],
    *,
    as_of_date: date,
    theme_key: str,
    theme_name: str,
) -> dict[str, Any]:
    active_events: list[Any] = []
    limitations: list[str] = []
    skipped = {"expired": 0, "unverified": 0, "pending": 0}
    for event in events:
        scoreable, reason = _event_is_scoreable(event, as_of_date)
        if scoreable:
            active_events.append(event)
            continue
        if reason == "事件已过有效期。":
            skipped["expired"] += 1
        elif reason == "事件尚未确认。":
            skipped["pending"] += 1
        else:
            skipped["unverified"] += 1
        if reason and reason not in limitations:
            limitations.append(reason)

    if not active_events:
        return {
            "theme_key": theme_key,
            "theme_name": theme_name,
            "status": "unavailable",
            "catalyst_score": NEUTRAL_COMPONENT_SCORE,
            "sentiment_heat_score": NEUTRAL_COMPONENT_SCORE,
            "event_count": 0,
            "key_events": [],
            "limitations": limitations or ["暂无可用于评分的主题催化事件。"],
            "score_breakdown": {
                "score_version": CATALYST_SCORE_VERSION,
                "active_event_count": 0,
                "skipped": skipped,
            },
        }

    weighted: list[float] = []
    recent_count = 0
    key_events: list[dict[str, Any]] = []
    proxy_theme = False
    for event in active_events:
        strength = _clamp(float(getattr(event, "strength_score", NEUTRAL_COMPONENT_SCORE) or NEUTRAL_COMPONENT_SCORE))
        confidence = _clamp(float(getattr(event, "confidence_score", NEUTRAL_COMPONENT_SCORE) or NEUTRAL_COMPONENT_SCORE))
        decay = _recency_decay(event, as_of_date)
        weighted.append(strength * (confidence / 100.0) * decay)
        if _event_date(event) and (as_of_date - _event_date(event)).days <= 45:
            recent_count += 1
        metadata = _metadata(event)
        proxy_theme = proxy_theme or bool(metadata.get("proxy_theme"))
        key_events.append(
            {
                "title": str(getattr(event, "title", "") or ""),
                "summary": str(getattr(event, "summary", "") or ""),
                "catalyst_type": str(getattr(event, "catalyst_type", "") or ""),
                "source_url": _source_url(event),
                "event_date": _event_date(event).isoformat() if _event_date(event) else None,
                "source_type": str(getattr(event, "source_type", "") or "manual"),
                "strength_score": strength,
                "confidence_score": confidence,
            }
        )

    avg_weighted = sum(weighted) / len(weighted)
    catalyst_score = _clamp(45.0 + avg_weighted * 0.45 + min(len(active_events), 5) * 3.0, high=95.0)
    sentiment_heat_score = _clamp(45.0 + min(len(active_events), 5) * 10.0 + min(recent_count, 3) * 8.0, high=95.0)
    if proxy_theme:
        catalyst_score = min(catalyst_score, 75.0)
        sentiment_heat_score = min(sentiment_heat_score, 75.0)
        limitations.append("该主题使用代理 ETF 观察，置信度已降低。")
    return {
        "theme_key": theme_key,
        "theme_name": theme_name,
        "status": "success",
        "catalyst_score": round(catalyst_score, 2),
        "sentiment_heat_score": round(sentiment_heat_score, 2),
        "event_count": len(active_events),
        "key_events": key_events[:3],
        "limitations": limitations,
        "score_breakdown": {
            "score_version": CATALYST_SCORE_VERSION,
            "active_event_count": len(active_events),
            "weighted_event_strength": round(avg_weighted, 2),
            "recent_event_count": recent_count,
            "proxy_theme": proxy_theme,
            "skipped": skipped,
        },
    }


async def seed_theme_catalyst_events(session: AsyncSession) -> dict[str, int]:
    inserted = 0
    updated = 0
    for seed in THEME_CATALYST_SEEDS:
        existing = await session.scalar(
            select(EtfThemeCatalystEvent).where(
                EtfThemeCatalystEvent.theme_key == seed.theme_key,
                EtfThemeCatalystEvent.title == seed.title,
                EtfThemeCatalystEvent.event_date == seed.event_date,
            )
        )
        if existing is None:
            session.add(
                EtfThemeCatalystEvent(
                    theme_key=seed.theme_key,
                    theme_name=seed.theme_name,
                    catalyst_type=seed.catalyst_type,
                    title=seed.title,
                    summary=seed.summary,
                    source_url=seed.source_url,
                    event_date=seed.event_date,
                    effective_start=seed.effective_start,
                    effective_end=seed.effective_end,
                    direction="positive",
                    strength_score=seed.strength_score,
                    confidence_score=seed.confidence_score,
                    status="active",
                    source_type="manual_seed",
                    metadata_json=seed.metadata,
                )
            )
            inserted += 1
            continue
        existing.theme_name = seed.theme_name
        existing.catalyst_type = seed.catalyst_type
        existing.summary = seed.summary
        existing.source_url = seed.source_url
        existing.effective_start = seed.effective_start
        existing.effective_end = seed.effective_end
        existing.strength_score = seed.strength_score
        existing.confidence_score = seed.confidence_score
        existing.status = "active"
        existing.source_type = "manual_seed"
        existing.metadata_json = seed.metadata
        updated += 1
    await session.flush()
    return {"inserted": inserted, "updated": updated}


async def refresh_theme_catalyst_snapshots(
    session: AsyncSession,
    *,
    as_of_date: date | None = None,
) -> dict[str, Any]:
    effective_date = as_of_date or date.today()
    seed_result = await seed_theme_catalyst_events(session)
    theme_names = {seed.theme_key: seed.theme_name for seed in THEME_CATALYST_SEEDS}
    rows = (
        await session.scalars(
            select(EtfThemeCatalystEvent).where(EtfThemeCatalystEvent.theme_key.in_(theme_names.keys()))
        )
    ).all()
    events_by_theme: dict[str, list[EtfThemeCatalystEvent]] = {key: [] for key in theme_names}
    for row in rows:
        events_by_theme.setdefault(row.theme_key, []).append(row)

    upserted = 0
    unavailable = 0
    for theme_key, events in events_by_theme.items():
        scored = score_theme_catalysts(
            events,
            as_of_date=effective_date,
            theme_key=theme_key,
            theme_name=theme_names.get(theme_key, theme_key),
        )
        existing = await session.scalar(
            select(EtfThemeCatalystSnapshot).where(
                EtfThemeCatalystSnapshot.as_of_date == effective_date,
                EtfThemeCatalystSnapshot.theme_key == theme_key,
            )
        )
        if existing is None:
            existing = EtfThemeCatalystSnapshot(as_of_date=effective_date, theme_key=theme_key, theme_name=scored["theme_name"])
            session.add(existing)
        existing.theme_name = str(scored["theme_name"])
        existing.status = str(scored["status"])
        existing.catalyst_score = float(scored["catalyst_score"])
        existing.sentiment_heat_score = float(scored["sentiment_heat_score"])
        existing.event_count = int(scored["event_count"])
        existing.key_events_json = list(scored["key_events"])
        existing.limitations_json = list(scored["limitations"])
        existing.score_breakdown_json = dict(scored["score_breakdown"])
        existing.generated_at = utcnow()
        upserted += 1
        if existing.status != "success":
            unavailable += 1
    await session.commit()
    return {
        "as_of_date": effective_date.isoformat(),
        "seeded": seed_result,
        "snapshots": upserted,
        "unavailable": unavailable,
        "themes": sorted(theme_names),
    }


async def latest_theme_catalyst_snapshots_by_key(
    session: AsyncSession,
    *,
    as_of_date: date,
) -> dict[str, EtfThemeCatalystSnapshot]:
    rows = (
        await session.scalars(
            select(EtfThemeCatalystSnapshot)
            .where(EtfThemeCatalystSnapshot.as_of_date <= as_of_date)
            .order_by(EtfThemeCatalystSnapshot.as_of_date.desc(), EtfThemeCatalystSnapshot.id.desc())
        )
    ).all()
    result: dict[str, EtfThemeCatalystSnapshot] = {}
    for row in rows:
        result.setdefault(row.theme_key, row)
    return result


def theme_keys_for_asset(
    *,
    asset_name: str,
    theme_tags: Sequence[str],
    theme_profile: Mapping[str, Any] | None,
) -> list[str]:
    profile = theme_profile or {}
    raw_parts = [
        asset_name,
        str(profile.get("primary_theme") or ""),
        str(profile.get("theme_group") or ""),
        " ".join(str(item) for item in profile.get("secondary_themes") or []),
        " ".join(theme_tags),
    ]
    text = " ".join(raw_parts)
    keys: list[str] = []
    if any(keyword in text for keyword in ("机器人", "具身智能")):
        keys.append("机器人")
    if any(keyword in text for keyword in ("创新药", "生物药", "生物医药", "港股创新药", "医药创新")):
        keys.append("创新药")
    if any(keyword in text for keyword in ("半导体", "芯片", "集成电路")):
        keys.append("半导体")
    if any(keyword in text for keyword in ("光模块", "CPO", "光通信")):
        keys.append("光模块")
    if any(keyword in text for keyword in ("通信", "5G", "信息技术")):
        keys.append("光模块_proxy")
    primary = str(profile.get("primary_theme") or "").strip()
    if primary and primary not in {"未分类", "unknown"}:
        keys.append(primary)
    return list(dict.fromkeys(keys))


def build_asset_opportunity_payload(
    *,
    asset_name: str,
    theme_tags: Sequence[str],
    metrics: Mapping[str, Any],
    risk_flags: Sequence[str],
    technical_score: float,
    snapshots_by_key: Mapping[str, EtfThemeCatalystSnapshot],
) -> dict[str, Any]:
    theme_profile = metrics.get("theme_profile")
    profile = theme_profile if isinstance(theme_profile, Mapping) else {}
    theme_keys = theme_keys_for_asset(asset_name=asset_name, theme_tags=theme_tags, theme_profile=profile)
    matched_snapshot = next((snapshots_by_key[key] for key in theme_keys if key in snapshots_by_key), None)
    if matched_snapshot is None:
        catalyst_score = NEUTRAL_COMPONENT_SCORE
        sentiment_score = NEUTRAL_COMPONENT_SCORE
        catalyst_summary = "暂无可用于评分的主题催化事件，先按技术结构观察。"
        limitations = ["主题催化数据不可用。"]
        events: list[dict[str, Any]] = []
        status = "unavailable"
        catalyst_theme_key = theme_keys[0] if theme_keys else None
        catalyst_theme_name = catalyst_theme_key or "未匹配主题"
        catalyst_breakdown: dict[str, Any] = {"score_version": CATALYST_SCORE_VERSION, "active_event_count": 0}
    else:
        catalyst_score = float(matched_snapshot.catalyst_score)
        sentiment_score = float(matched_snapshot.sentiment_heat_score)
        events = list(matched_snapshot.key_events_json or [])
        catalyst_summary = events[0]["summary"] if events else "主题催化事件已记录，但缺少摘要。"
        limitations = [
            *(matched_snapshot.limitations_json or []),
            "热度分为主题事件热度，非实时舆情热度。",
        ]
        status = matched_snapshot.status
        catalyst_theme_key = matched_snapshot.theme_key
        catalyst_theme_name = matched_snapshot.theme_name
        catalyst_breakdown = dict(matched_snapshot.score_breakdown_json or {})

    sector_score_raw = metrics.get("sector_trend_score")
    sector_score = float(sector_score_raw) if isinstance(sector_score_raw, int | float) else None
    sector_available = str(metrics.get("sector_trend_status") or "") == "success" and sector_score is not None
    catalyst_available = status == "success"
    risk_set = set(risk_flags)
    entry_label = str(metrics.get("entry_timing_label") or "")
    default_display_eligible = bool(metrics.get("default_display_eligible", True))
    data_limited = bool(risk_set.intersection(DATA_LIMITING_RISKS) or not default_display_eligible)

    opportunity_score: float | None
    opportunity_version: str
    components: dict[str, dict[str, float | None]]
    weights: dict[str, float]
    if data_limited:
        opportunity_score = None
        opportunity_version = OPPORTUNITY_SCORE_UNAVAILABLE_VERSION
        components = {}
        weights = {}
    elif sector_available and catalyst_available:
        opportunity_score = _clamp(
            float(technical_score) * FULL_TECHNICAL_WEIGHT
            + float(sector_score) * FULL_SECTOR_WEIGHT
            + catalyst_score * FULL_CATALYST_WEIGHT
            + sentiment_score * FULL_SENTIMENT_WEIGHT
        )
        opportunity_version = OPPORTUNITY_SCORE_FULL_VERSION
        components = {
            "technical": {"score": round(float(technical_score), 2), "weight": FULL_TECHNICAL_WEIGHT},
            "sector_trend": {"score": round(float(sector_score), 2), "weight": FULL_SECTOR_WEIGHT},
            "theme_catalyst": {"score": round(catalyst_score, 2), "weight": FULL_CATALYST_WEIGHT},
            "news_sentiment_heat": {"score": round(sentiment_score, 2), "weight": FULL_SENTIMENT_WEIGHT},
        }
        weights = {
            "technical": FULL_TECHNICAL_WEIGHT,
            "sector_trend": FULL_SECTOR_WEIGHT,
            "theme_catalyst": FULL_CATALYST_WEIGHT,
            "news_sentiment_heat": FULL_SENTIMENT_WEIGHT,
        }
    elif sector_available:
        opportunity_score = _clamp(
            float(technical_score) * SECTOR_ONLY_TECHNICAL_WEIGHT + float(sector_score) * SECTOR_ONLY_WEIGHT
        )
        opportunity_version = OPPORTUNITY_SCORE_SECTOR_ONLY_VERSION
        components = {
            "technical": {"score": round(float(technical_score), 2), "weight": SECTOR_ONLY_TECHNICAL_WEIGHT},
            "sector_trend": {"score": round(float(sector_score), 2), "weight": SECTOR_ONLY_WEIGHT},
        }
        weights = {"technical": SECTOR_ONLY_TECHNICAL_WEIGHT, "sector_trend": SECTOR_ONLY_WEIGHT}
    elif catalyst_available:
        opportunity_score = _clamp(
            float(technical_score) * TECHNICAL_WEIGHT
            + catalyst_score * CATALYST_WEIGHT
            + sentiment_score * SENTIMENT_WEIGHT
        )
        opportunity_version = OPPORTUNITY_SCORE_VERSION
        components = {
            "technical": {"score": round(float(technical_score), 2), "weight": TECHNICAL_WEIGHT},
            "theme_catalyst": {"score": round(catalyst_score, 2), "weight": CATALYST_WEIGHT},
            "news_sentiment_heat": {"score": round(sentiment_score, 2), "weight": SENTIMENT_WEIGHT},
        }
        weights = {
            "technical": TECHNICAL_WEIGHT,
            "theme_catalyst": CATALYST_WEIGHT,
            "news_sentiment_heat": SENTIMENT_WEIGHT,
        }
    else:
        opportunity_score = None
        opportunity_version = OPPORTUNITY_SCORE_UNAVAILABLE_VERSION
        components = {}
        weights = {}

    if data_limited:
        opportunity_label = "等待数据"
        limitations = [*limitations, "数据或流动性限制未解除，催化不能提高到决策状态。"]
    elif entry_label in RISK_ENTRY_LABELS and (catalyst_score >= 70 or (sector_score or 0) >= 75):
        opportunity_label = "主题强但等买点"
        limitations = [*limitations, f"当前买点为{entry_label}，主题催化不能覆盖追高风险。"]
    elif catalyst_available and catalyst_score >= 75 and opportunity_score is not None and opportunity_score >= 70:
        opportunity_label = "重点观察"
    elif sector_available and not catalyst_available:
        opportunity_label = "板块强但等催化" if (sector_score or 0) >= 70 else "板块观察"
    elif opportunity_score is None:
        opportunity_label = "暂无综合关注"
    elif status == "unavailable":
        opportunity_label = "技术优先"
    else:
        opportunity_label = "常规观察"

    return {
        "metrics": {
            "technical_score": round(float(technical_score), 2),
            "opportunity_score": opportunity_score,
            "opportunity_label": opportunity_label,
            "catalyst_score": round(catalyst_score, 2),
            "sentiment_heat_score": round(sentiment_score, 2),
            "catalyst_summary": catalyst_summary,
            "catalyst_events": events,
            "catalyst_limitations": list(dict.fromkeys(limitations)),
            "catalyst_status": status,
            "catalyst_theme_key": catalyst_theme_key,
            "catalyst_theme_name": catalyst_theme_name,
            "opportunity_score_version": opportunity_version,
        },
        "breakdown": {
            "score_version": opportunity_version,
            "opportunity_score": opportunity_score,
            "opportunity_label": opportunity_label,
            "components": components,
            "weights": weights,
            "sector_trend": {
                "score": round(float(sector_score), 2) if sector_score is not None else None,
                "status": str(metrics.get("sector_trend_status") or "unavailable"),
                "label": metrics.get("sector_trend_label"),
            },
            "catalyst": catalyst_breakdown,
            "limitations": list(dict.fromkeys(limitations)),
        },
    }
