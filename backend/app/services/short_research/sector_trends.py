from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

SECTOR_TREND_SCORE_VERSION = "sector_trend_v1"
MIN_VALID_PEERS = 2
UNKNOWN_THEMES = {"", "unknown", "未分类", "UNKNOWN"}
DATA_LIMITING_RISKS = {"数据不足", "数据滞后", "流动性不足"}


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return round(max(low, min(high, value)), 2)


def _float(value: Any) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _metadata(asset: Any) -> Any:
    return getattr(asset, "metadata", None)


def _code(asset: Any) -> str:
    metadata = _metadata(asset)
    return str(getattr(metadata, "code", "") or "")


def _metrics(asset: Any) -> Mapping[str, Any]:
    value = getattr(asset, "metrics", None)
    return value if isinstance(value, Mapping) else {}


def _risk_flags(asset: Any) -> set[str]:
    return {str(item) for item in getattr(asset, "risk_flags", []) or []}


def _theme_key(asset: Any) -> str | None:
    metrics = _metrics(asset)
    profile = metrics.get("theme_profile")
    if not isinstance(profile, Mapping):
        profile = {}
    for key in (profile.get("primary_theme"), profile.get("theme_group")):
        text = str(key or "").strip()
        if text not in UNKNOWN_THEMES:
            return text
    metadata = _metadata(asset)
    for tag in getattr(metadata, "theme_tags", []) or []:
        text = str(tag or "").strip()
        if text not in UNKNOWN_THEMES:
            return text
    return None


def _is_valid_peer(asset: Any) -> bool:
    metrics = _metrics(asset)
    if _risk_flags(asset).intersection(DATA_LIMITING_RISKS):
        return False
    if not bool(metrics.get("default_display_eligible", True)):
        return False
    required = (
        "return_5d",
        "return_20d",
        "average_turnover_20d",
    )
    if any(_float(metrics.get(key)) is None for key in required):
        return False
    technical = _float(metrics.get("technical_score")) or _float(getattr(asset, "total_score", None))
    return technical is not None


def _label(score: float) -> str:
    if score >= 80:
        return "板块强势"
    if score >= 70:
        return "板块升温"
    if score >= 55:
        return "板块分化"
    return "板块偏弱"


def _unavailable_payload(reason: str, *, theme: str | None = None, peer_count: int = 0) -> dict[str, Any]:
    return {
        "metrics": {
            "sector_trend_score": None,
            "sector_trend_label": "暂无板块趋势",
            "sector_trend_summary": reason,
            "sector_trend_reason": reason,
            "sector_peer_count": peer_count,
            "sector_trend_status": "unavailable",
            "sector_trend_theme": theme,
        },
        "breakdown": {
            "score_version": SECTOR_TREND_SCORE_VERSION,
            "status": "unavailable",
            "reason": reason,
            "peer_count": peer_count,
            "theme": theme,
        },
    }


def _score_theme(theme: str, peers: Sequence[Any]) -> dict[str, Any]:
    count = len(peers)
    returns_5d = [_float(_metrics(peer).get("return_5d")) or 0.0 for peer in peers]
    returns_20d = [_float(_metrics(peer).get("return_20d")) or 0.0 for peer in peers]
    technical_scores = [
        _float(_metrics(peer).get("technical_score")) or _float(getattr(peer, "total_score", None)) or 50.0
        for peer in peers
    ]
    ma5_positive = sum(1 for peer in peers if (_float(_metrics(peer).get("distance_to_ma5_pct")) or 0.0) >= 0)
    ma20_positive = sum(1 for peer in peers if (_float(_metrics(peer).get("distance_to_ma20_pct")) or 0.0) >= 0)
    return_positive = sum(1 for value in returns_5d if value > 0)

    avg_5d = sum(returns_5d) / count
    avg_20d = sum(returns_20d) / count
    avg_technical = sum(technical_scores) / count
    turnover_ratios: list[float] = []
    for peer in peers:
        metrics = _metrics(peer)
        turnover_20d = _float(metrics.get("average_turnover_20d")) or 0.0
        turnover_60d = _float(metrics.get("average_turnover_60d"))
        if turnover_60d and turnover_60d > 0:
            turnover_ratios.append(turnover_20d / turnover_60d)
    avg_turnover_ratio = sum(turnover_ratios) / len(turnover_ratios) if turnover_ratios else 1.0

    return_score = _clamp(50.0 + avg_5d * 250.0 + avg_20d * 125.0)
    breadth_score = _clamp(((return_positive / count) + (ma5_positive / count) + (ma20_positive / count)) / 3 * 100)
    turnover_score = _clamp(50.0 + (avg_turnover_ratio - 1.0) * 50.0, high=90.0)
    score = _clamp(return_score * 0.35 + breadth_score * 0.30 + avg_technical * 0.20 + turnover_score * 0.15)
    label = _label(score)
    summary = (
        f"{theme}上涨家数 {return_positive}/{count}，"
        f"5日均值 {avg_5d * 100:.1f}%，20日均值 {avg_20d * 100:.1f}%，{label}。"
    )
    return {
        "metrics": {
            "sector_trend_score": score,
            "sector_trend_label": label,
            "sector_trend_summary": summary,
            "sector_trend_reason": summary,
            "sector_peer_count": count,
            "sector_trend_status": "success",
            "sector_trend_theme": theme,
        },
        "breakdown": {
            "score_version": SECTOR_TREND_SCORE_VERSION,
            "status": "success",
            "theme": theme,
            "peer_count": count,
            "components": {
                "recent_return": {
                    "score": return_score,
                    "weight": 0.35,
                    "avg_5d": round(avg_5d, 6),
                    "avg_20d": round(avg_20d, 6),
                },
                "breadth": {
                    "score": breadth_score,
                    "weight": 0.30,
                    "positive_return_count": return_positive,
                    "ma5_positive_count": ma5_positive,
                    "ma20_positive_count": ma20_positive,
                },
                "technical": {"score": round(avg_technical, 2), "weight": 0.20},
                "turnover": {
                    "score": turnover_score,
                    "weight": 0.15,
                    "avg_turnover_ratio": round(avg_turnover_ratio, 4),
                },
            },
        },
    }


def build_sector_trend_payloads(assets: Sequence[Any]) -> dict[str, dict[str, Any]]:
    themes: dict[str, list[Any]] = {}
    payloads: dict[str, dict[str, Any]] = {}
    for asset in assets:
        code = _code(asset)
        theme = _theme_key(asset)
        if theme is None:
            payloads[code] = _unavailable_payload("未分类主题无法计算板块趋势。")
            continue
        themes.setdefault(theme, []).append(asset)

    for theme, theme_assets in themes.items():
        valid_peers = [asset for asset in theme_assets if _is_valid_peer(asset)]
        if len(valid_peers) < MIN_VALID_PEERS:
            payload = _unavailable_payload(
                f"{theme}有效样本不足，暂无板块趋势数据。",
                theme=theme,
                peer_count=len(valid_peers),
            )
        else:
            payload = _score_theme(theme, valid_peers)
        for asset in theme_assets:
            payloads[_code(asset)] = payload
    return payloads
