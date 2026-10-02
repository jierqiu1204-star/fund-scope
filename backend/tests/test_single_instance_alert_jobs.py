from __future__ import annotations

from datetime import date

import pytest

from app.models.entities import TrackedPosition, User
from app.services.tracked_positions import jobs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "job", [jobs.daily_tracked_position_alerts_job, jobs.intraday_tracked_position_alerts_job]
)
@pytest.mark.parametrize("use_legacy_owner", [False, True])
async def test_alert_jobs_only_send_for_the_selected_instance_owner(
    app, monkeypatch, job, use_legacy_owner
) -> None:
    sent_to = []

    async def skip_entry_refresh(session, position):
        pass

    async def send_alert(session, position, settings, *, evaluation_mode="daily", **kwargs):
        owner = await session.get(User, position.user_id)
        sent_to.append((owner.id, owner.recipient_email))
        return None, "email_sent"

    monkeypatch.setattr(jobs, "refresh_entry_if_waiting", skip_entry_refresh)
    monkeypatch.setattr(jobs, "create_alert_if_needed", send_alert)

    async with app.state.db.session() as session:
        original_owner = await session.get(User, 1)
        historical_user = User(
            email="historical-owner@example.com",
            recipient_email="historical-recipient@example.com",
        )
        session.add(historical_user)
        await session.flush()
        for owner, code in [(original_owner, "510300"), (historical_user, "512800")]:
            session.add(
                TrackedPosition(
                    user_id=owner.id,
                    asset_type="etf",
                    asset_code=code,
                    asset_name=code,
                    buy_date=date(2026, 6, 1),
                    buy_amount=1000,
                    status="active",
                )
            )
        await session.commit()
        app.state.settings.auth_bootstrap_admin_email = (
            historical_user.email if use_legacy_owner else ""
        )
        selected = historical_user if use_legacy_owner else original_owner
        result = await job(session, app.state.settings)

        assert sent_to == [(selected.id, selected.recipient_email)]
        assert result["positions_checked"] == 1
        assert result["emails_sent"] == 1
