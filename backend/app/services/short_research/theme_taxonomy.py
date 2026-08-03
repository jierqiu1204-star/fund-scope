from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import EtfThemeProfile, TradableEtf, utcnow

UNKNOWN_THEME = "未分类"
UNKNOWN_GROUP = "unknown"
UNKNOWN_BUCKET = "unknown"


@dataclass(frozen=True)
class EtfThemeProfileData:
    etf_code: str
    asset_bucket: str
    theme_group: str
    primary_theme: str
    secondary_themes: list[str]
    classification_source: str
    classification_confidence: str
    classification_reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "asset_bucket": self.asset_bucket,
            "theme_group": self.theme_group,
            "primary_theme": self.primary_theme,
            "secondary_themes": list(self.secondary_themes),
            "classification_source": self.classification_source,
            "classification_confidence": self.classification_confidence,
            "classification_reason": self.classification_reason,
        }


_MANUAL_OVERRIDES: dict[str, tuple[str, str, str, list[str], str]] = {
    "513520": ("cross_border", "cross_border", "日经", ["日本", "跨境"], "日经 ETF 代码覆盖"),
    "159941": ("cross_border", "cross_border", "纳指", ["纳斯达克", "跨境"], "纳指 ETF 代码覆盖"),
    "159632": ("cross_border", "cross_border", "纳指", ["纳斯达克", "跨境"], "纳指 ETF 代码覆盖"),
    "512800": ("equity", "financial", "银行", ["金融"], "银行 ETF 代码覆盖"),
    "512880": ("equity", "financial", "证券", ["券商", "金融"], "证券 ETF 代码覆盖"),
    "512000": ("equity", "financial", "证券", ["券商", "金融"], "证券 ETF 代码覆盖"),
    "515070": ("equity", "technology", "人工智能", ["AI", "科技"], "AI ETF 代码覆盖"),
    "515000": ("equity", "technology", "科技", ["科技龙头"], "科技 ETF 代码覆盖"),
    "588220": ("equity", "technology", "科创创业", ["科创", "创业板", "科技"], "科创创业 ETF 代码覆盖"),
    "511010": ("bond", "bond", "国债", ["债券", "低波动"], "国债 ETF 代码覆盖"),
    "518880": ("commodity", "gold", "黄金", ["商品"], "黄金 ETF 代码覆盖"),
}

_CROSS_BORDER_KEYWORD_RULES: list[tuple[tuple[str, ...], str, str, str, list[str]]] = [
    (("纳斯达克",), "cross_border", "cross_border", "纳指", ["纳斯达克", "跨境"]),
    (("纳指",), "cross_border", "cross_border", "纳指", ["纳斯达克", "跨境"]),
    (("恒生科技",), "cross_border", "cross_border", "恒生科技", ["港股", "跨境"]),
    (
        ("恒生", "港股", "中概", "日经", "标普", "德国", "法国"),
        "cross_border",
        "cross_border",
        "跨境",
        ["海外市场"],
    ),
]

_DOMESTIC_KEYWORD_RULES: list[tuple[tuple[str, ...], str, str, str, list[str]]] = [
    (("半导体", "芯片", "集成电路"), "equity", "technology", "半导体", ["芯片", "科技"]),
    (("机器人", "具身智能", "人形机器人", "工业机器人"), "equity", "technology", "机器人", ["高端制造", "人工智能"]),
    (("人工智能", "AI", "云计算", "软件", "计算机"), "equity", "technology", "人工智能", ["AI", "科技"]),
    (("科技", "科创", "创业板", "双创"), "equity", "technology", "科技", ["成长"]),
    (("新能源", "光伏", "电池", "储能", "电力设备"), "equity", "energy", "新能源", ["电力设备"]),
    (("电力",), "equity", "energy", "电力", ["公用事业"]),
    (("煤炭",), "equity", "energy", "煤炭", ["能源"]),
    (("化工", "有色金属", "有色"), "equity", "materials", "基础材料", ["周期"]),
    (("创新药", "生物药", "生物医药", "港股创新药", "医药创新"), "equity", "healthcare", "创新药", ["医药"]),
    (("医药", "医疗", "生物"), "equity", "healthcare", "生物医药", ["医药"]),
    (("证券", "券商"), "equity", "financial", "证券", ["金融"]),
    (("银行",), "equity", "financial", "银行", ["金融"]),
    (("红利", "低波", "股息"), "equity", "dividend", "红利", ["低波动"]),
    (("游戏", "传媒"), "equity", "technology", "游戏传媒", ["数字内容"]),
    (("养殖",), "equity", "consumer", "养殖", ["农业", "消费"]),
    (("消费", "食品", "白酒", "酒"), "equity", "consumer", "消费", ["内需"]),
    (("房地产",), "equity", "financial", "房地产", ["地产"]),
    (("智能汽车",), "equity", "industrial", "智能汽车", ["汽车", "制造"]),
    (("基建",), "equity", "industrial", "基建", ["制造"]),
    (("军工", "高端制造", "机械"), "equity", "industrial", "高端制造", ["制造"]),
    (("黄金",), "commodity", "gold", "黄金", ["商品"]),
    (("债", "国债", "政金债", "可转债"), "bond", "bond", "债券", ["低波动"]),
    (("货币", "现金", "添益", "保证金"), "money", "money", "货币", ["现金管理"]),
    (("沪深300", "上证50", "中证500", "中证1000", "A500", "宽基"), "broad_base", "broad_base", "宽基", ["指数"]),
]

_ASSET_CLASS_BUCKETS = {
    "bond": ("bond", "bond", "债券"),
    "commodity": ("commodity", "gold", "商品"),
    "cross_border": ("cross_border", "cross_border", "跨境"),
    "broad_index": ("broad_base", "broad_base", "宽基"),
    "money": ("money", "money", "货币"),
    "sector": ("equity", "unknown", UNKNOWN_THEME),
}


def _dedupe(values: list[str]) -> list[str]:
    return [item for item in dict.fromkeys(value for value in values if value and value != UNKNOWN_THEME)]


def classify_etf_theme(
    *,
    code: str,
    name: str,
    asset_class: str | None = None,
    theme_tags: list[str] | tuple[str, ...] | None = None,
    authoritative_profile: EtfThemeProfileData | None = None,
) -> EtfThemeProfileData:
    if authoritative_profile is not None:
        return EtfThemeProfileData(
            etf_code=code,
            asset_bucket=authoritative_profile.asset_bucket,
            theme_group=authoritative_profile.theme_group,
            primary_theme=authoritative_profile.primary_theme,
            secondary_themes=_dedupe(authoritative_profile.secondary_themes),
            classification_source="authoritative",
            classification_confidence=authoritative_profile.classification_confidence,
            classification_reason=authoritative_profile.classification_reason,
        )

    tags = list(theme_tags or [])
    if code in _MANUAL_OVERRIDES:
        bucket, group, primary, secondary, reason = _MANUAL_OVERRIDES[code]
        return EtfThemeProfileData(
            etf_code=code,
            asset_bucket=bucket,
            theme_group=group,
            primary_theme=primary,
            secondary_themes=_dedupe(secondary),
            classification_source="manual_override",
            classification_confidence="high",
            classification_reason=reason,
        )

    text = " ".join([name, *tags])
    for keywords, bucket, group, primary, secondary in _CROSS_BORDER_KEYWORD_RULES:
        matched = next((keyword for keyword in keywords if keyword in text), None)
        if matched:
            return EtfThemeProfileData(
                etf_code=code,
                asset_bucket=bucket,
                theme_group=group,
                primary_theme=primary,
                secondary_themes=_dedupe([*secondary, *tags]),
                classification_source="cross_border_keyword",
                classification_confidence="high" if matched in name else "medium",
                classification_reason=f"跨境名称或标签命中“{matched}”。",
            )

    if asset_class in _ASSET_CLASS_BUCKETS:
        bucket, group, primary = _ASSET_CLASS_BUCKETS[asset_class]
        if primary != UNKNOWN_THEME:
            return EtfThemeProfileData(
                etf_code=code,
                asset_bucket=bucket,
                theme_group=group,
                primary_theme=primary,
                secondary_themes=_dedupe(tags),
                classification_source="asset_class",
                classification_confidence="medium",
                classification_reason=f"资产类别为 {asset_class}，但名称没有更细主题证据。",
            )

    for keywords, bucket, group, primary, secondary in _DOMESTIC_KEYWORD_RULES:
        matched = next((keyword for keyword in keywords if keyword in text), None)
        if matched:
            return EtfThemeProfileData(
                etf_code=code,
                asset_bucket=bucket,
                theme_group=group,
                primary_theme=primary,
                secondary_themes=_dedupe([*secondary, *tags]),
                classification_source="domestic_keyword",
                classification_confidence="high" if matched in name else "medium",
                classification_reason=f"国内名称或标签命中“{matched}”。",
            )

    return EtfThemeProfileData(
        etf_code=code,
        asset_bucket=UNKNOWN_BUCKET,
        theme_group=UNKNOWN_GROUP,
        primary_theme=UNKNOWN_THEME,
        secondary_themes=[],
        classification_source="unknown",
        classification_confidence="unknown",
        classification_reason="名称、标签和资产类别都没有命中可审计主题规则。",
    )


def profile_from_row(row: EtfThemeProfile | None, *, fallback: EtfThemeProfileData | None = None) -> dict[str, Any]:
    if row is None:
        return (fallback or classify_etf_theme(code="", name="")).as_dict()
    return {
        "asset_bucket": row.asset_bucket,
        "theme_group": row.theme_group,
        "primary_theme": row.primary_theme,
        "secondary_themes": list(row.secondary_themes_json or []),
        "classification_source": row.classification_source,
        "classification_confidence": row.classification_confidence,
        "classification_reason": row.classification_reason or "",
    }


async def theme_profiles_by_code(session: AsyncSession, codes: list[str]) -> dict[str, EtfThemeProfile]:
    if not codes:
        return {}
    rows = (await session.scalars(select(EtfThemeProfile).where(EtfThemeProfile.etf_code.in_(codes)))).all()
    return {row.etf_code: row for row in rows}


async def refresh_etf_theme_profiles(session: AsyncSession) -> dict[str, Any]:
    rows = (await session.scalars(select(TradableEtf).order_by(TradableEtf.code.asc()))).all()
    inserted = 0
    updated = 0
    failed: list[dict[str, str]] = []
    classified = 0
    unknown = 0
    low_confidence = 0

    existing = await theme_profiles_by_code(session, [row.code for row in rows])
    now = utcnow()
    for etf in rows:
        try:
            profile = classify_etf_theme(
                code=etf.code,
                name=etf.name,
                asset_class=etf.asset_class,
                theme_tags=list(etf.theme_tags_json or []),
            )
            if profile.primary_theme == UNKNOWN_THEME:
                unknown += 1
            else:
                classified += 1
            if profile.classification_confidence in {"low", "unknown"}:
                low_confidence += 1
            row = existing.get(etf.code)
            if row is None:
                session.add(
                    EtfThemeProfile(
                        etf_code=etf.code,
                        asset_bucket=profile.asset_bucket,
                        theme_group=profile.theme_group,
                        primary_theme=profile.primary_theme,
                        secondary_themes_json=list(profile.secondary_themes),
                        classification_source=profile.classification_source,
                        classification_confidence=profile.classification_confidence,
                        classification_reason=profile.classification_reason,
                        created_at=now,
                        updated_at=now,
                    )
                )
                inserted += 1
            else:
                row.asset_bucket = profile.asset_bucket
                row.theme_group = profile.theme_group
                row.primary_theme = profile.primary_theme
                row.secondary_themes_json = list(profile.secondary_themes)
                row.classification_source = profile.classification_source
                row.classification_confidence = profile.classification_confidence
                row.classification_reason = profile.classification_reason
                row.updated_at = now
                updated += 1

            merged_tags = _dedupe(
                [
                    *(etf.theme_tags_json or []),
                    profile.primary_theme,
                    profile.theme_group,
                    profile.asset_bucket,
                    *profile.secondary_themes,
                ]
            )
            if merged_tags:
                etf.theme_tags_json = merged_tags
        except Exception as exc:  # noqa: BLE001
            failed.append({"code": etf.code, "error": str(exc)})

    await session.commit()
    latest_refresh = await session.scalar(select(func.max(EtfThemeProfile.updated_at)))
    return {
        "total": len(rows),
        "classified": classified,
        "unknown": unknown,
        "low_confidence": low_confidence,
        "inserted": inserted,
        "updated": updated,
        "failed": len(failed),
        "failures": failed[:20],
        "latest_taxonomy_refresh": latest_refresh.isoformat() if isinstance(latest_refresh, datetime) else None,
    }


async def theme_coverage_summary(session: AsyncSession) -> dict[str, Any]:
    total = int(await session.scalar(select(func.count()).select_from(TradableEtf)) or 0)
    profile_total = int(await session.scalar(select(func.count()).select_from(EtfThemeProfile)) or 0)
    unknown = int(
        await session.scalar(
            select(func.count()).select_from(EtfThemeProfile).where(EtfThemeProfile.primary_theme == UNKNOWN_THEME)
        )
        or 0
    )
    low_confidence = int(
        await session.scalar(
            select(func.count())
            .select_from(EtfThemeProfile)
            .where(EtfThemeProfile.classification_confidence.in_(["low", "unknown"]))
        )
        or 0
    )
    latest_refresh = await session.scalar(select(func.max(EtfThemeProfile.updated_at)))
    return {
        "total": total,
        "profile_total": profile_total,
        "classified": max(profile_total - unknown, 0),
        "unknown": unknown,
        "low_confidence": low_confidence,
        "latest_taxonomy_refresh": latest_refresh.isoformat() if isinstance(latest_refresh, datetime) else None,
    }
