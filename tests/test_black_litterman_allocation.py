from app.services.portfolio_allocation import (
    BLACK_LITTERMAN_METHOD,
    BlackLittermanCandidate,
    black_litterman_covariance_summary,
    build_black_litterman_allocation,
)


def _returns(seed: float, count: int = 80) -> tuple[float, ...]:
    return tuple(seed + ((index % 5) - 2) * 0.0005 for index in range(count))


def _candidate(
    code: str,
    *,
    theme: str = "科技",
    score: float = 86.0,
    data_reliability: str = "verified",
    returns: tuple[float, ...] | None = None,
) -> BlackLittermanCandidate:
    sample = returns or _returns(0.001)
    return BlackLittermanCandidate(
        code=code,
        name=code,
        theme_group=theme,
        score=score,
        observation_label="短线观察",
        entry_timing_label="健康回踩",
        returns=sample,
        expected_return=sum(sample[-60:]) / 60,
        volatility=0.02,
        liquidity_score=100.0,
        validation_sample_count=80,
        data_reliability=data_reliability,
    )


def test_black_litterman_excludes_stale_candidates() -> None:
    candidates = [
        _candidate("A", theme="科技"),
        _candidate("B", theme="金融"),
        _candidate("C", theme="红利"),
        _candidate("D", theme="黄金"),
        _candidate("E", theme="医药", data_reliability="stale"),
    ]

    result = build_black_litterman_allocation(candidates)

    assert result.status == "success"
    assert all(item.code != "E" for item in result.items)
    assert any(item["code"] == "E" and "stale" in item["reason"] for item in result.excluded_items)


def test_black_litterman_respects_single_and_theme_caps() -> None:
    candidates = [
        _candidate("A", theme="科技", score=98),
        _candidate("B", theme="科技", score=96),
        _candidate("C", theme="金融", score=82),
        _candidate("D", theme="红利", score=80),
        _candidate("E", theme="黄金", score=78),
    ]

    result = build_black_litterman_allocation(candidates)

    assert result.status == "success"
    assert result.summary["method"] == BLACK_LITTERMAN_METHOD
    assert round(sum(item.target_weight for item in result.items), 4) == 1.0
    assert max(item.target_weight for item in result.items) <= 0.3
    tech_weight = sum(item.target_weight for item in result.items if item.theme_group == "科技")
    assert tech_weight <= 0.6


def test_black_litterman_returns_unavailable_for_short_history() -> None:
    candidates = [
        _candidate("A", theme="科技", returns=_returns(0.001, count=20)),
        _candidate("B", theme="金融", returns=_returns(0.001, count=20)),
        _candidate("C", theme="红利", returns=_returns(0.001, count=20)),
        _candidate("D", theme="黄金", returns=_returns(0.001, count=20)),
    ]

    result = build_black_litterman_allocation(candidates)
    covariance = black_litterman_covariance_summary(candidates)

    assert result.status == "unavailable"
    assert result.items == ()
    assert covariance["status"] == "unavailable"
