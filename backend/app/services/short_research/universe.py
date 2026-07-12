from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

import akshare as ak
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.defaults.short_research import DEFAULT_SHORT_RESEARCH_ETFS
from app.models.entities import EtfUniverseMembership, TradableEtf
from app.services.short_research.ranking_contract import canonical_hash

EXCLUDED_ETF_KEYWORDS = (
    "货币",
    "现金",
    "添益",
    "保证金",
    "LOF",
    "lof",
    "封闭",
    "定开",
    "持有",
    "一年",
    "滚动",
)


@dataclass(frozen=True)
class EtfUniverseRecord:
    code: str
    name: str
    exchange: str
    category: str
    theme_tags: list[str]
    trading_rule_label: str
    source: str


@dataclass(frozen=True)
class PointInTimeUniverseSnapshot:
    as_of_date: date
    members: list[dict[str, Any]]
    universe_snapshot_hash: str


def normalize_etf_code(value: Any) -> str:
    raw = str(value or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) >= 6:
        return digits[-6:]
    return digits


def infer_exchange(code: str, fallback: str | None = None) -> str:
    if fallback:
        value = fallback.upper()
        if value in {"SH", "SZ"}:
            return value
        if "上海" in fallback or "SH" in value:
            return "SH"
        if "深圳" in fallback or "SZ" in value:
            return "SZ"
    return "SH" if code.startswith(("5", "6")) else "SZ"


def is_tradable_etf_code(code: str) -> bool:
    return len(code) == 6 and code[:3] in {
        "510",
        "511",
        "512",
        "513",
        "515",
        "516",
        "517",
        "518",
        "520",
        "560",
        "561",
        "562",
        "563",
        "588",
        "589",
        "159",
    }


def is_short_term_etf_eligible(name: str, *, code: str = "") -> bool:
    if not is_tradable_etf_code(code):
        return False
    if not name or any(keyword in name for keyword in EXCLUDED_ETF_KEYWORDS):
        return False
    return "ETF" in name.upper()


async def build_point_in_time_universe_snapshot(
    session: AsyncSession,
    *,
    as_of_date: date,
) -> PointInTimeUniverseSnapshot:
    rows = (
        await session.execute(
            select(TradableEtf, EtfUniverseMembership)
            .join(EtfUniverseMembership, EtfUniverseMembership.etf_code == TradableEtf.code)
            .where(
                EtfUniverseMembership.effective_from <= as_of_date,
                or_(
                    EtfUniverseMembership.effective_to.is_(None),
                    EtfUniverseMembership.effective_to >= as_of_date,
                ),
            )
            .order_by(TradableEtf.code.asc(), EtfUniverseMembership.effective_from.desc())
        )
    ).all()
    members_by_code: dict[str, dict[str, Any]] = {}
    for etf, membership in rows:
        members_by_code.setdefault(
            etf.code,
            {
                "asset_code": etf.code,
                "asset_bucket": etf.asset_class,
                "theme_tags": sorted(etf.theme_tags_json or []),
                "tracked_underlying_id": membership.tracked_underlying_id,
                "membership_source": membership.source,
                "effective_from": membership.effective_from,
                "effective_to": membership.effective_to,
            },
        )
    members = [members_by_code[code] for code in sorted(members_by_code)]
    return PointInTimeUniverseSnapshot(
        as_of_date=as_of_date,
        members=members,
        universe_snapshot_hash=canonical_hash(members),
    )


def classify_etf(name: str) -> tuple[str, list[str], str, str]:
    tags: list[str] = []
    category = "other"
    direction = "交易所 ETF"
    rule = "证券账户 T+1 ETF"

    theme_map = [
        ("半导体", "芯片", "科技"),
        ("芯片", "芯片", "科技"),
        ("人工智能", "AI", "科技"),
        ("AI", "AI", "科技"),
        ("机器人", "机器人", "高端制造"),
        ("新能源", "新能源", "电力设备"),
        ("光伏", "光伏", "新能源"),
        ("电池", "电池", "新能源"),
        ("证券", "金融", "证券"),
        ("银行", "金融", "银行"),
        ("红利", "红利", "低波动"),
        ("医药", "医药", "防御"),
        ("医疗", "医疗", "医药"),
        ("消费", "消费", "内需"),
        ("酒", "消费", "白酒"),
        ("黄金", "黄金", "避险"),
        ("纳斯达克", "跨境", "纳斯达克"),
        ("恒生", "港股", "跨境"),
        ("中概", "中概", "互联网"),
        ("科创", "科创", "科技"),
        ("创业板", "创业板", "成长"),
        ("沪深300", "宽基", "沪深300"),
        ("中证500", "宽基", "中证500"),
        ("中证1000", "宽基", "小盘"),
        ("债", "债券", "低波动"),
    ]
    for keyword, *mapped_tags in theme_map:
        if keyword in name:
            tags.extend(mapped_tags)

    if any(tag in tags for tag in ["纳斯达克", "港股", "中概"]):
        category = "cross_border"
        direction = "跨境市场 ETF"
        rule = "证券账户 ETF，跨境品种价格可能受汇率和海外市场影响"
    elif "债券" in tags:
        category = "bond"
        direction = "债券 ETF"
    elif "黄金" in tags:
        category = "commodity"
        direction = "商品 ETF"
        rule = "证券账户 ETF，商品品种可能支持更灵活交易规则"
    elif "宽基" in tags or any(word in name for word in ["50ETF", "300ETF", "500ETF", "1000ETF"]):
        category = "broad_index"
        direction = "宽基指数 ETF"
    elif tags:
        category = "sector"
        direction = "行业主题 ETF"

    deduped = list(dict.fromkeys(tags)) or ["ETF"]
    return category, deduped, direction, rule


def _record_from_default(item: Any) -> EtfUniverseRecord:
    return EtfUniverseRecord(
        code=item.code,
        name=item.name,
        exchange=item.exchange or infer_exchange(item.code),
        category=item.category,
        theme_tags=list(item.theme_tags),
        trading_rule_label=item.trading_rule_label,
        source="default_seed",
    )


def _pick(record: Any, *keys: str) -> Any:
    for key in keys:
        if key in record and record[key] not in (None, ""):
            return record[key]
    return None


def normalize_source_row(record: Any, *, source: str) -> EtfUniverseRecord | None:
    code = normalize_etf_code(_pick(record, "代码", "基金代码", "symbol", "code"))
    name = str(_pick(record, "名称", "基金简称", "基金名称", "name") or "").strip()
    if not code or not name:
        return None
    category, theme_tags, _, rule = classify_etf(name)
    return EtfUniverseRecord(
        code=code,
        name=name,
        exchange=infer_exchange(code, str(_pick(record, "市场", "exchange") or "")),
        category=category,
        theme_tags=theme_tags,
        trading_rule_label=rule,
        source=source,
    )


async def discover_etf_universe() -> list[EtfUniverseRecord]:
    records: list[EtfUniverseRecord] = []
    try:
        frame = await asyncio.to_thread(ak.fund_etf_spot_em)
        for _, row in frame.iterrows():
            record = normalize_source_row(row, source="akshare.fund_etf_spot_em")
            if record is not None:
                records.append(record)
    except Exception:  # noqa: BLE001
        records = []

    seeded = [_record_from_default(item) for item in DEFAULT_SHORT_RESEARCH_ETFS]
    by_code = {item.code: item for item in seeded}
    for item in records:
        by_code[item.code] = item
    return sorted(by_code.values(), key=lambda item: item.code)


async def refresh_etf_universe(
    session: AsyncSession,
    *,
    records: Iterable[EtfUniverseRecord] | None = None,
) -> dict[str, Any]:
    discovered = list(records) if records is not None else await discover_etf_universe()
    inserted = 0
    updated = 0
    excluded = 0
    failed: list[dict[str, str]] = []

    for record in discovered:
        try:
            eligible = is_short_term_etf_eligible(record.name, code=record.code)
            if not eligible:
                excluded += 1
            existing = await session.scalar(select(TradableEtf).where(TradableEtf.code == record.code))
            if existing is None:
                session.add(
                    TradableEtf(
                        code=record.code,
                        name=record.name,
                        exchange=record.exchange,
                        theme_tags_json=list(record.theme_tags),
                        trading_rule_label=record.trading_rule_label,
                        asset_class=record.category,
                        is_short_term_eligible=eligible,
                        is_watchlist=eligible,
                    )
                )
                inserted += 1
            else:
                existing.name = record.name
                existing.exchange = record.exchange or existing.exchange
                if not existing.theme_tags_json:
                    existing.theme_tags_json = list(record.theme_tags)
                existing.trading_rule_label = record.trading_rule_label or existing.trading_rule_label
                existing.asset_class = record.category or existing.asset_class
                existing.is_short_term_eligible = eligible
                existing.is_watchlist = bool(existing.is_watchlist or eligible)
                updated += 1
        except Exception as exc:  # noqa: BLE001
            failed.append({"code": record.code, "error": str(exc)})

    await session.commit()
    default_display = await session.scalar(
        select(func.count()).select_from(TradableEtf).where(
            TradableEtf.is_short_term_eligible.is_(True),
            TradableEtf.is_watchlist.is_(True),
        )
    )
    return {
        "discovered": len(discovered),
        "inserted": inserted,
        "updated": updated,
        "excluded": excluded,
        "default_display": int(default_display or 0),
        "failed": len(failed),
        "failures": failed,
    }
