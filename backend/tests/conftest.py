from __future__ import annotations

import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.auth import create_access_token, hash_password
from app.core.config import Settings
from app.db.base import Base
from app.defaults.funds import DEFAULT_RESEARCH_FUNDS
from app.main import create_app
from app.models.entities import Fund, Index, Portfolio, User


@pytest.fixture
def tmp_path() -> AsyncIterator[Path]:
    temp_dir = Path(__file__).resolve().parents[2] / ".test-tmp" / uuid4().hex
    temp_dir.mkdir(parents=True, exist_ok=True)

    yield temp_dir

    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    db_path = tmp_path / "test.db"
    return Settings(
        database_url=f"sqlite+aiosqlite:///{db_path}",
        openai_base_url="https://example.com/v1",
        openai_api_key="test-key",
        model_name="test-model",
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_username="mailer@example.com",
        smtp_password="secret",
        smtp_from="FundScope <mailer@example.com>",
        cors_origins=["http://localhost:3000"],
    )


@pytest.fixture
async def app(settings: Settings):
    application = create_app(settings=settings, start_scheduler=False)

    async with application.state.db.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with application.state.db.session() as session:
        session.add(
            User(
                id=1,
                email="19535838578@163.com",
                password_hash=hash_password("test-password"),
                display_name="qje",
                is_approved=True,
                is_super_admin=True,
                recipient_email="19535838578@163.com",
                reminder_day=1,
                reference_index_code="CSI300",
                base_monthly_amount=833.0,
                smtp_host="smtp.example.com",
                smtp_port=587,
                smtp_username="mailer@example.com",
                smtp_password_ref="env:SMTP_PASSWORD",
                smtp_from="FundScope <mailer@example.com>",
            )
        )
        session.add(Portfolio(id=1, user_id=1, name="Default Portfolio", is_default=True))
        session.add_all(
            [
                Fund(
                    code=fund.code,
                    name=fund.name,
                    category=fund.category,
                    tracking_index_code=fund.tracking_index_code,
                    target_allocation=fund.target_allocation,
                    is_watchlist=True,
                )
                for fund in DEFAULT_RESEARCH_FUNDS
            ]
        )
        session.add_all(
            [
                Index(code="CSI300", name="CSI 300", region="CN", is_watchlist=True),
                Index(code="CSI500", name="CSI 500", region="CN", is_watchlist=True),
                Index(code="CSI800", name="CSI 800", region="CN", is_watchlist=True),
                Index(code="CHINEXT", name="ChiNext", region="CN", is_watchlist=True),
                Index(code="SP500", name="S&P 500", region="US", is_watchlist=True),
                Index(code="NDX100", name="Nasdaq 100", region="US", is_watchlist=True),
            ]
        )
        await session.commit()

    yield application

    await application.state.db.engine.dispose()


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as test_client:
        async with app.state.db.session() as session:
            user = await session.get(User, 1)
            assert user is not None
            token, _ = create_access_token(user, app.state.settings)
        test_client.headers["Authorization"] = f"Bearer {token}"
        yield test_client
