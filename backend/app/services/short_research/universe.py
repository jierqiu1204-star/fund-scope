from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any

import akshare as ak
import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    EtfListingDateObservation,
    EtfUniverseMembership,
    TradableEtf,
    utcnow,
)
from app.services.intraday_etf.service import fetch_eastmoney_etf_spot_rows
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
LIVE_DISCOVERY_MIN_RETENTION_RATIO = 0.8
EASTMONEY_UNIVERSE_SOURCE = "eastmoney.push2.clist"
AKSHARE_UNIVERSE_SOURCE = "akshare.fund_etf_spot_em"
ETF_UNIVERSE_PROVIDER_TIMEOUT_SECONDS = 20.0
SSE_LISTING_SOURCE = "sse.etf.fundlist"
SSE_LISTING_PROVIDER_VERSION = "COMMON_JJZWZ_JJLB_L_v1"
SSE_LISTING_URL = "https://query.sse.com.cn/commonQuery.do"
SZSE_LISTING_SOURCE = "szse.fund.etf.list"
SZSE_LISTING_PROVIDER_VERSION = "CATALOGID_1000_lf_v1"


@dataclass(frozen=True)
class EtfUniverseRecord:
    code: str
    name: str
    exchange: str
    category: str
    theme_tags: list[str]
    trading_rule_label: str
    source: str
    listing_date: date | None = None
    listing_date_source: str | None = None
    listing_provider_version: str | None = None
    listing_universe_snapshot_hash: str | None = None
    listing_raw_payload_hash: str | None = None


@dataclass(frozen=True)
class OfficialListingMetadataSource:
    exchange: str
    source: str
    provider_version: str
    status: str
    dates_by_code: Mapping[str, date]
    source_row_count: int
    expected_total: int | None
    universe_snapshot_hash: str | None
    raw_payload_hash: str | None
    error_summary: str | None = None

    @property
    def authoritative(self) -> bool:
        return self.status == "authoritative"


@dataclass(frozen=True)
class EtfUniverseDiscovery:
    records: tuple[EtfUniverseRecord, ...]
    status: str
    source: str
    source_row_count: int
    normalized_row_count: int
    error_summary: str | None = None

    @property
    def authoritative(self) -> bool:
        return self.status == "authoritative"


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


def _pick(record: Any, *keys: str) -> Any:
    for key in keys:
        if key in record and record[key] not in (None, ""):
            return record[key]
    return None


def parse_provider_listing_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = str(value or "").strip()
    if not raw or raw in {"-", "--", "0"}:
        return None
    digits = "".join(character for character in raw if character.isdigit())
    try:
        if len(digits) == 8:
            return datetime.strptime(digits, "%Y%m%d").date()
        return date.fromisoformat(raw[:10].replace("/", "-"))
    except ValueError:
        return None


def _listing_source_failure(
    *,
    exchange: str,
    source: str,
    provider_version: str,
    exc: Exception,
) -> OfficialListingMetadataSource:
    return OfficialListingMetadataSource(
        exchange=exchange,
        source=source,
        provider_version=provider_version,
        status="failure",
        dates_by_code={},
        source_row_count=0,
        expected_total=None,
        universe_snapshot_hash=None,
        raw_payload_hash=None,
        error_summary=f"{type(exc).__name__}: {exc}"[:500],
    )


async def _discover_sse_official_listing_metadata() -> OfficialListingMetadataSource:
    params = {
        "isPagination": "true",
        "sqlId": SSE_LISTING_PROVIDER_VERSION.removesuffix("_v1"),
        "pageHelp.pageSize": "10000",
        "pageHelp.pageNo": "1",
        "pageHelp.beginPage": "1",
        "pageHelp.endPage": "1",
        "CATEGORY": "F000",
        "CATEGORY_ASC": "1",
        "type": "inParams",
    }
    headers = {
        "Referer": "https://etf.sse.com.cn/fundlist/",
        "User-Agent": "Mozilla/5.0",
    }
    try:
        async with httpx.AsyncClient(
            timeout=ETF_UNIVERSE_PROVIDER_TIMEOUT_SECONDS,
            headers=headers,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
        ) as client:
            response = await client.get(SSE_LISTING_URL, params=params)
            response.raise_for_status()
            payload = response.json()
        rows = payload.get("result")
        if not isinstance(rows, list):
            raise ValueError("SSE listing response has no result list")
        page_help = payload.get("pageHelp") or {}
        expected_total = int(page_help.get("total") or 0)
        normalized: list[dict[str, str]] = []
        dates_by_code: dict[str, date] = {}
        invalid_count = 0
        for row in rows:
            if not isinstance(row, Mapping):
                invalid_count += 1
                continue
            code = normalize_etf_code(row.get("FUND_CODE"))
            listing_date = parse_provider_listing_date(row.get("LISTING_DATE"))
            if not code or listing_date is None or code in dates_by_code:
                invalid_count += 1
                continue
            dates_by_code[code] = listing_date
            normalized.append(
                {
                    "code": code,
                    "listing_date": listing_date.isoformat(),
                }
            )
        complete = (
            expected_total > 0
            and len(rows) == expected_total
            and invalid_count == 0
            and len(dates_by_code) == expected_total
        )
        errors = [] if complete else [
            f"expected={expected_total}",
            f"received={len(rows)}",
            f"valid={len(dates_by_code)}",
            f"invalid={invalid_count}",
        ]
        return OfficialListingMetadataSource(
            exchange="SH",
            source=SSE_LISTING_SOURCE,
            provider_version=SSE_LISTING_PROVIDER_VERSION,
            status="authoritative" if complete else "partial",
            dates_by_code=dates_by_code,
            source_row_count=len(rows),
            expected_total=expected_total,
            universe_snapshot_hash=canonical_hash(sorted(normalized, key=lambda item: item["code"])),
            raw_payload_hash=canonical_hash(payload),
            error_summary="; ".join(errors) or None,
        )
    except Exception as exc:  # noqa: BLE001
        return _listing_source_failure(
            exchange="SH",
            source=SSE_LISTING_SOURCE,
            provider_version=SSE_LISTING_PROVIDER_VERSION,
            exc=exc,
        )


async def _discover_szse_official_listing_metadata() -> OfficialListingMetadataSource:
    try:
        async with asyncio.timeout(ETF_UNIVERSE_PROVIDER_TIMEOUT_SECONDS):
            frame = await asyncio.to_thread(ak.fund_etf_scale_szse)
        rows = frame.to_dict(orient="records")
        dates_by_code: dict[str, date] = {}
        normalized: list[dict[str, str]] = []
        invalid_count = 0
        raw_rows: list[dict[str, str]] = []
        for row in rows:
            raw_rows.append({str(key): str(value) for key, value in row.items()})
            code = normalize_etf_code(_pick(row, "基金代码", "代码", "code"))
            listing_date = parse_provider_listing_date(
                _pick(row, "上市日期", "上市时间", "listing_date")
            )
            if not code or listing_date is None or code in dates_by_code:
                invalid_count += 1
                continue
            dates_by_code[code] = listing_date
            normalized.append(
                {
                    "code": code,
                    "listing_date": listing_date.isoformat(),
                }
            )
        complete = bool(rows) and invalid_count == 0 and len(dates_by_code) == len(rows)
        return OfficialListingMetadataSource(
            exchange="SZ",
            source=SZSE_LISTING_SOURCE,
            provider_version=SZSE_LISTING_PROVIDER_VERSION,
            status="authoritative" if complete else "partial",
            dates_by_code=dates_by_code,
            source_row_count=len(rows),
            expected_total=len(rows),
            universe_snapshot_hash=canonical_hash(sorted(normalized, key=lambda item: item["code"])),
            raw_payload_hash=canonical_hash(raw_rows),
            error_summary=(
                None
                if complete
                else f"received={len(rows)}; valid={len(dates_by_code)}; invalid={invalid_count}"
            ),
        )
    except Exception as exc:  # noqa: BLE001
        return _listing_source_failure(
            exchange="SZ",
            source=SZSE_LISTING_SOURCE,
            provider_version=SZSE_LISTING_PROVIDER_VERSION,
            exc=exc,
        )


async def enrich_with_official_listing_metadata(
    records: Iterable[EtfUniverseRecord],
) -> tuple[tuple[EtfUniverseRecord, ...], dict[str, Any]]:
    frozen_records = tuple(records)
    sse, szse = await asyncio.gather(
        _discover_sse_official_listing_metadata(),
        _discover_szse_official_listing_metadata(),
    )
    sources = {"SH": sse, "SZ": szse}
    enriched: list[EtfUniverseRecord] = []
    matched = 0
    for record in frozen_records:
        snapshot = sources.get(record.exchange)
        listing_date = (
            snapshot.dates_by_code.get(record.code)
            if snapshot is not None and snapshot.authoritative
            else None
        )
        if listing_date is not None and snapshot is not None:
            matched += 1
            enriched.append(
                replace(
                    record,
                    listing_date=listing_date,
                    listing_date_source=snapshot.source,
                    listing_provider_version=snapshot.provider_version,
                    listing_universe_snapshot_hash=snapshot.universe_snapshot_hash,
                    listing_raw_payload_hash=snapshot.raw_payload_hash,
                )
            )
        else:
            enriched.append(
                replace(
                    record,
                    listing_date=None,
                    listing_date_source=None,
                    listing_provider_version=None,
                    listing_universe_snapshot_hash=None,
                    listing_raw_payload_hash=None,
                )
            )
    expected = len(frozen_records)
    return tuple(enriched), {
        "source_kind": "official_exchange_complete_snapshot",
        "expected_count": expected,
        "observed_count": matched,
        "coverage_ratio": matched / expected if expected else 0.0,
        "sources": [
            {
                "exchange": snapshot.exchange,
                "source": snapshot.source,
                "provider_version": snapshot.provider_version,
                "status": snapshot.status,
                "source_row_count": snapshot.source_row_count,
                "expected_total": snapshot.expected_total,
                "universe_snapshot_hash": snapshot.universe_snapshot_hash,
                "raw_payload_hash": snapshot.raw_payload_hash,
                "error_summary": snapshot.error_summary,
            }
            for snapshot in (sse, szse)
        ],
    }


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
        listing_date=parse_provider_listing_date(
            _pick(record, "上市日期", "上市时间", "listing_date", "list_date")
        ),
        listing_date_source=source,
    )


def _finalize_discovery(
    records: list[EtfUniverseRecord],
    *,
    source: str,
    source_row_count: int,
    expected_total: int | None,
    provider_complete: bool,
    provider_error: str | None = None,
) -> EtfUniverseDiscovery:
    by_code = {item.code: item for item in records}
    normalized = tuple(sorted(by_code.values(), key=lambda item: item.code))
    reasons: list[str] = []
    if provider_error:
        reasons.append(provider_error)
    if expected_total is None or expected_total <= 0:
        reasons.append("provider did not report a positive expected total")
    elif source_row_count != expected_total:
        reasons.append(f"expected {expected_total} rows, received {source_row_count}")
    elif not provider_complete:
        reasons.append("provider marked the universe snapshot incomplete")
    if len(records) != source_row_count:
        reasons.append(f"{source_row_count - len(records)} provider rows were invalid")
    if len(normalized) != len(records):
        reasons.append(f"{len(records) - len(normalized)} duplicate ETF codes were returned")

    if not normalized:
        status = "failure"
        if not reasons:
            reasons.append("provider returned no usable ETF universe rows")
    elif reasons:
        status = "partial"
    else:
        status = "authoritative"
    return EtfUniverseDiscovery(
        records=normalized,
        status=status,
        source=source,
        source_row_count=source_row_count,
        normalized_row_count=len(normalized),
        error_summary="; ".join(reasons) or None,
    )


async def _discover_eastmoney_universe() -> EtfUniverseDiscovery:
    source = EASTMONEY_UNIVERSE_SOURCE
    result = await fetch_eastmoney_etf_spot_rows()
    records: list[EtfUniverseRecord] = []
    for row in result.rows:
        market = str(row.get("f13") or "")
        exchange = "SH" if market == "1" else ("SZ" if market == "0" else "")
        record = normalize_source_row(
            {
                "code": row.get("f12"),
                "name": row.get("f14"),
                "exchange": exchange,
                "listing_date": row.get("f26"),
            },
            source=source,
        )
        if record is not None:
            records.append(record)
    return _finalize_discovery(
        records,
        source=source,
        source_row_count=len(result.rows),
        expected_total=result.expected_total,
        provider_complete=result.complete,
        provider_error=result.error,
    )


async def _discover_akshare_universe() -> EtfUniverseDiscovery:
    source = AKSHARE_UNIVERSE_SOURCE
    records: list[EtfUniverseRecord] = []
    try:
        async with asyncio.timeout(ETF_UNIVERSE_PROVIDER_TIMEOUT_SECONDS):
            frame = await asyncio.to_thread(ak.fund_etf_spot_em)
        source_row_count = int(len(frame.index))
        for _, row in frame.iterrows():
            record = normalize_source_row(row, source=source)
            if record is not None:
                records.append(record)
    except Exception as exc:  # noqa: BLE001
        return EtfUniverseDiscovery(
            records=(),
            status="failure",
            source=source,
            source_row_count=0,
            normalized_row_count=0,
            error_summary=f"{type(exc).__name__}: {exc}"[:500],
        )
    return _finalize_discovery(
        records,
        source=source,
        source_row_count=source_row_count,
        expected_total=source_row_count,
        provider_complete=True,
    )


async def discover_etf_universe() -> EtfUniverseDiscovery:
    primary = await _discover_eastmoney_universe()
    if primary.authoritative:
        return primary
    fallback = await _discover_akshare_universe()
    if fallback.authoritative:
        return fallback

    candidates = (primary, fallback)
    best = max(
        candidates,
        key=lambda item: (item.status == "partial", item.normalized_row_count, item.source_row_count),
    )
    errors = [
        f"{item.source}: {item.error_summary or item.status}"
        for item in candidates
    ]
    return EtfUniverseDiscovery(
        records=best.records,
        status="partial" if best.records else "failure",
        source=",".join(item.source for item in candidates),
        source_row_count=best.source_row_count,
        normalized_row_count=best.normalized_row_count,
        error_summary="; ".join(errors)[:1000],
    )


async def refresh_etf_universe(
    session: AsyncSession,
    *,
    records: Iterable[EtfUniverseRecord] | None = None,
    as_of_date: date | None = None,
) -> dict[str, Any]:
    live_discovery = records is None
    if records is None:
        discovery = await discover_etf_universe()
    else:
        explicit_records = tuple(records)
        discovery = EtfUniverseDiscovery(
            records=explicit_records,
            status="authoritative",
            source="explicit",
            source_row_count=len(explicit_records),
            normalized_row_count=len(explicit_records),
        )
    discovered = list(discovery.records)
    effective_date = as_of_date or date.today()
    listing_metadata_evidence: dict[str, Any] = {
        "source_kind": "not_observed",
        "expected_count": len(discovered),
        "observed_count": 0,
        "coverage_ratio": 0.0,
        "sources": [],
    }
    inserted = 0
    updated = 0
    excluded = 0
    activated = 0
    deactivated = 0
    failed: list[dict[str, str]] = []
    active_memberships = {
        membership.etf_code: membership
        for membership in (
            await session.scalars(select(EtfUniverseMembership).where(EtfUniverseMembership.effective_to.is_(None)))
        ).all()
    }
    eligible_codes = {
        record.code
        for record in discovered
        if is_short_term_etf_eligible(record.name, code=record.code)
    }
    authoritative = discovery.authoritative
    discovery_status = discovery.status
    discovery_error = discovery.error_summary
    active_codes = set(active_memberships)
    if (
        authoritative
        and live_discovery
        and active_memberships
        and len(eligible_codes & active_codes) / len(active_codes) < LIVE_DISCOVERY_MIN_RETENTION_RATIO
    ):
        authoritative = False
        discovery_status = "partial"
        discovery_error = (
            "live discovery retained fewer than "
            f"{LIVE_DISCOVERY_MIN_RETENTION_RATIO:.0%} of active memberships"
        )
    if authoritative and live_discovery:
        enriched, listing_metadata_evidence = await enrich_with_official_listing_metadata(
            discovered
        )
        discovered = list(enriched)

    if not authoritative:
        default_display = await session.scalar(
            select(func.count()).select_from(TradableEtf).where(
                TradableEtf.is_short_term_eligible.is_(True),
                TradableEtf.is_watchlist.is_(True),
            )
        )
        return {
            "as_of_date": effective_date.isoformat(),
            "discovered": len(discovered),
            "inserted": 0,
            "updated": 0,
            "activated": 0,
            "deactivated": 0,
            "excluded": 0,
            "default_display": int(default_display or 0),
            "failed": 0,
            "failures": [],
            "authoritative": False,
            "discovery_status": discovery_status,
            "discovery_source": discovery.source,
            "discovery_error": discovery_error,
            "source_row_count": discovery.source_row_count,
            "normalized_row_count": discovery.normalized_row_count,
            "stale_universe": True,
            "listing_metadata": listing_metadata_evidence,
        }
    memberships_to_activate: list[EtfUniverseRecord] = []
    listing_observed_at = utcnow()
    existing_etfs = {
        etf.code: etf
        for etf in (await session.scalars(select(TradableEtf))).all()
    }

    for record in discovered:
        try:
            eligible = is_short_term_etf_eligible(record.name, code=record.code)
            if not eligible:
                excluded += 1
            existing = existing_etfs.get(record.code)
            if existing is None:
                existing = TradableEtf(
                    code=record.code,
                    name=record.name,
                    exchange=record.exchange,
                    theme_tags_json=list(record.theme_tags),
                    trading_rule_label=record.trading_rule_label,
                    asset_class=record.category,
                    is_short_term_eligible=eligible,
                    is_watchlist=eligible,
                    listing_date=record.listing_date,
                    listing_date_source=(
                        (record.listing_date_source or record.source)
                        if record.listing_date is not None
                        else None
                    ),
                    listing_date_observed_at=(listing_observed_at if record.listing_date else None),
                )
                session.add(existing)
                existing_etfs[record.code] = existing
                inserted += 1
            else:
                existing.name = record.name
                existing.exchange = record.exchange or existing.exchange
                if not existing.theme_tags_json:
                    existing.theme_tags_json = list(record.theme_tags)
                existing.trading_rule_label = record.trading_rule_label or existing.trading_rule_label
                existing.asset_class = record.category or existing.asset_class
                existing.is_short_term_eligible = eligible
                existing.is_watchlist = eligible
                if record.listing_date is not None and (
                    not live_discovery
                    or (
                        record.listing_provider_version
                        and record.listing_universe_snapshot_hash
                        and record.listing_raw_payload_hash
                    )
                ):
                    existing.listing_date = record.listing_date
                    existing.listing_date_source = record.listing_date_source or record.source
                    existing.listing_date_observed_at = listing_observed_at
                updated += 1
            active_membership = active_memberships.get(record.code)
            if eligible and active_membership is None:
                memberships_to_activate.append(record)
            elif not eligible and active_membership is not None:
                active_membership.effective_to = effective_date
                active_membership.exclusion_reason = "ineligible_from_refresh"
                deactivated += 1
        except Exception as exc:  # noqa: BLE001
            failed.append({"code": record.code, "error": str(exc)})

    for code, existing in existing_etfs.items():
        if code not in eligible_codes:
            existing.is_short_term_eligible = False
            existing.is_watchlist = False

    for code, membership in active_memberships.items():
        if code in eligible_codes or membership.effective_to is not None:
            continue
        membership.effective_to = effective_date
        membership.exclusion_reason = "missing_from_refresh"
        deactivated += 1

    await session.flush()
    observation_records = [
        record
        for record in discovered
        if record.listing_date is not None
        and record.listing_date_source
        and record.listing_provider_version
        and record.listing_universe_snapshot_hash
        and record.listing_raw_payload_hash
    ]
    evidence_by_code = {
        record.code: canonical_hash(
            {
                "code": record.code,
                "exchange": record.exchange,
                "listing_date": record.listing_date,
                "source": record.listing_date_source,
                "provider_version": record.listing_provider_version,
                "universe_snapshot_hash": record.listing_universe_snapshot_hash,
                "raw_payload_hash": record.listing_raw_payload_hash,
            }
        )
        for record in observation_records
    }
    existing_evidence = set(
        await session.scalars(
            select(EtfListingDateObservation.evidence_hash).where(
                EtfListingDateObservation.evidence_hash.in_(tuple(evidence_by_code.values()))
            )
        )
    ) if evidence_by_code else set()
    for record in observation_records:
        evidence_hash = evidence_by_code[record.code]
        if evidence_hash in existing_evidence:
            continue
        session.add(
            EtfListingDateObservation(
                etf_code=record.code,
                exchange=record.exchange,
                listing_date=record.listing_date,
                source=str(record.listing_date_source),
                provider_version=str(record.listing_provider_version),
                observed_at=listing_observed_at,
                universe_snapshot_hash=str(record.listing_universe_snapshot_hash),
                raw_payload_hash=str(record.listing_raw_payload_hash),
                evidence_hash=evidence_hash,
            )
        )
    for record in memberships_to_activate:
        session.add(
            EtfUniverseMembership(
                etf_code=record.code,
                effective_from=effective_date,
                source=record.source,
            )
        )
        activated += 1
    await session.commit()
    default_display = await session.scalar(
        select(func.count()).select_from(TradableEtf).where(
            TradableEtf.is_short_term_eligible.is_(True),
            TradableEtf.is_watchlist.is_(True),
        )
    )
    return {
        "as_of_date": effective_date.isoformat(),
        "discovered": len(discovered),
        "inserted": inserted,
        "updated": updated,
        "activated": activated,
        "deactivated": deactivated,
        "excluded": excluded,
        "default_display": int(default_display or 0),
        "failed": len(failed),
        "failures": failed,
        "authoritative": True,
        "discovery_status": "authoritative",
        "discovery_source": discovery.source,
        "discovery_error": None,
        "source_row_count": discovery.source_row_count,
        "normalized_row_count": discovery.normalized_row_count,
        "stale_universe": False,
        "listing_metadata": listing_metadata_evidence,
    }
