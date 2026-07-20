from app.services.intraday_etf.service import (
    AKSHARE_PROVIDER_TIMEOUT_SECONDS,
    EASTMONEY_PROVIDER_TIMEOUT_SECONDS,
    EASTMONEY_PROVIDER_TOTAL_TIMEOUT_SECONDS,
    QUOTE_PROVIDER_TIMEOUT_SECONDS,
)
from app.services.short_etf.data import ETF_HISTORY_PROVIDER_TIMEOUT_SECONDS
from app.services.short_research.service import (
    ETF_RANKING_BATCH_SIZE,
    ETF_RANKING_BATCH_TIMEOUT_SECONDS,
)
from app.services.short_research.universe import (
    ETF_UNIVERSE_PROVIDER_TIMEOUT_SECONDS,
)


def test_ranking_workflow_provider_and_batch_boundaries_do_not_exceed_55_seconds() -> None:
    assert ETF_RANKING_BATCH_SIZE <= 20
    assert 0 < ETF_RANKING_BATCH_TIMEOUT_SECONDS <= 55
    assert 0 < ETF_HISTORY_PROVIDER_TIMEOUT_SECONDS <= 55
    assert 0 < ETF_UNIVERSE_PROVIDER_TIMEOUT_SECONDS <= 55
    assert all(
        0 < timeout <= 55
        for timeout in (
            QUOTE_PROVIDER_TIMEOUT_SECONDS,
            AKSHARE_PROVIDER_TIMEOUT_SECONDS,
            EASTMONEY_PROVIDER_TIMEOUT_SECONDS,
            EASTMONEY_PROVIDER_TOTAL_TIMEOUT_SECONDS,
        )
    )
