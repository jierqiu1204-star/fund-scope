from __future__ import annotations

import csv
from datetime import date
from io import StringIO
from typing import Literal, cast

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db_session
from app.models.entities import Fund, Portfolio, Transaction
from app.schemas.transactions import (
    CsvImportResponse,
    TransactionCreate,
    TransactionList,
    TransactionRead,
)

router = APIRouter(prefix="/api/transactions", tags=["transactions"])


async def _default_portfolio_id(session: AsyncSession) -> int:
    portfolio = await session.scalar(select(Portfolio).where(Portfolio.is_default.is_(True)))
    if portfolio is None:
        raise HTTPException(status_code=500, detail="Default portfolio is missing.")
    return portfolio.id


async def _validate_fund(session: AsyncSession, fund_code: str) -> None:
    fund = await session.get(Fund, fund_code)
    if fund is None:
        raise HTTPException(status_code=422, detail="Fund must first be added to the watchlist.")


def _build_transaction(payload: TransactionCreate, portfolio_id: int) -> Transaction:
    if payload.action == "buy":
        assert payload.amount is not None
        shares = (payload.amount - payload.fee) / payload.nav_at_trade
        return Transaction(
            portfolio_id=portfolio_id,
            fund_code=payload.fund_code,
            action=payload.action,
            amount=payload.amount,
            shares=shares,
            proceeds=None,
            nav_at_trade=payload.nav_at_trade,
            fee=payload.fee,
            traded_at=payload.traded_at,
        )
    assert payload.shares is not None
    proceeds = (payload.shares * payload.nav_at_trade) - payload.fee
    return Transaction(
        portfolio_id=portfolio_id,
        fund_code=payload.fund_code,
        action=payload.action,
        amount=proceeds,
        shares=payload.shares,
        proceeds=proceeds,
        nav_at_trade=payload.nav_at_trade,
        fee=payload.fee,
        traded_at=payload.traded_at,
    )


def _row_value(row: dict[str, str], *columns: str) -> str:
    for column in columns:
        value = row.get(column)
        if value is not None and value.strip():
            return value.strip()
    return ""


def _optional_float(value: str) -> float | None:
    if not value:
        return None
    return float(value.replace(",", ""))


def _required_float(value: str, field_name: str) -> float:
    parsed = _optional_float(value)
    if parsed is None:
        raise ValueError(f"{field_name} is required")
    return parsed


def _parse_alipay_action(value: str) -> Literal["buy", "sell"]:
    normalized = value.strip().lower()
    if normalized in {"buy", "申购", "买入", "定投"} or "买" in normalized or "申购" in normalized:
        return "buy"
    if normalized in {"sell", "redeem", "赎回", "卖出"} or "卖" in normalized or "赎回" in normalized:
        return "sell"
    raise ValueError(f"Unsupported Alipay transaction type: {value}")


def _parse_alipay_date(value: str) -> date:
    normalized = value.strip().replace("/", "-")
    if " " in normalized:
        normalized = normalized.split(" ", 1)[0]
    return date.fromisoformat(normalized)


def _parse_alipay_transaction(row: dict[str, str]) -> TransactionCreate:
    action = _parse_alipay_action(_row_value(row, "交易类型", "业务类型", "类型", "action"))
    nav = _required_float(_row_value(row, "成交净值", "确认净值", "净值", "单位净值", "nav"), "nav")
    fee = _optional_float(_row_value(row, "手续费", "费用", "fee")) or 0.0
    traded_at = _parse_alipay_date(_row_value(row, "交易日期", "确认日期", "申请日期", "traded_at"))
    amount = _optional_float(_row_value(row, "金额", "成交金额", "确认金额", "amount"))
    shares = _optional_float(_row_value(row, "份额", "确认份额", "shares"))
    return TransactionCreate(
        fund_code=_row_value(row, "基金代码", "产品代码", "fund_code"),
        action=action,
        amount=amount if action == "buy" else None,
        shares=shares if action == "sell" else None,
        nav_at_trade=nav,
        fee=fee,
        traded_at=traded_at,
    )


@router.post("", response_model=TransactionRead, status_code=status.HTTP_201_CREATED)
async def create_transaction(
    payload: TransactionCreate,
    session: AsyncSession = Depends(get_db_session),
) -> TransactionRead:
    await _validate_fund(session, payload.fund_code)
    transaction = _build_transaction(payload, await _default_portfolio_id(session))
    session.add(transaction)
    await session.commit()
    await session.refresh(transaction)
    return TransactionRead.model_validate(transaction, from_attributes=True)


@router.get("", response_model=TransactionList)
async def list_transactions(session: AsyncSession = Depends(get_db_session)) -> TransactionList:
    items = (await session.scalars(select(Transaction).order_by(Transaction.traded_at.desc(), Transaction.id.desc()))).all()
    total = await session.scalar(select(func.count()).select_from(Transaction)) or 0
    return TransactionList(total=total, items=[TransactionRead.model_validate(item, from_attributes=True) for item in items])


@router.post("/import-csv", response_model=CsvImportResponse)
async def import_transactions_csv(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_db_session),
) -> CsvImportResponse | JSONResponse:
    content = (await file.read()).decode("utf-8-sig")
    reader = csv.DictReader(StringIO(content))
    rows = list(reader)
    errors: list[dict[str, str | int]] = []
    parsed: list[TransactionCreate] = []

    for index, row in enumerate(rows, start=2):
        try:
            action = cast(Literal["buy", "sell"], row["action"])
            payload = TransactionCreate(
                fund_code=row["fund_code"],
                action=action,
                amount=float(row["amount_or_shares"]) if action == "buy" else None,
                shares=float(row["amount_or_shares"]) if action == "sell" else None,
                nav_at_trade=float(row["nav"]),
                fee=float(row["fee"]),
                traded_at=date.fromisoformat(row["traded_at"]),
            )
            await _validate_fund(session, payload.fund_code)
            parsed.append(payload)
        except Exception as exc:  # noqa: BLE001
            errors.append({"row": index, "message": str(exc)})

    if errors:
        return JSONResponse(status_code=422, content={"inserted": 0, "errors": errors})

    portfolio_id = await _default_portfolio_id(session)
    for payload in parsed:
        session.add(_build_transaction(payload, portfolio_id))
    await session.commit()
    return CsvImportResponse(inserted=len(parsed))


@router.post("/import-alipay-csv", response_model=CsvImportResponse)
async def import_alipay_transactions_csv(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_db_session),
) -> CsvImportResponse | JSONResponse:
    content = (await file.read()).decode("utf-8-sig")
    reader = csv.DictReader(StringIO(content))
    rows = list(reader)
    errors: list[dict[str, str | int]] = []
    parsed: list[TransactionCreate] = []

    for index, row in enumerate(rows, start=2):
        try:
            payload = _parse_alipay_transaction(row)
            await _validate_fund(session, payload.fund_code)
            parsed.append(payload)
        except Exception as exc:  # noqa: BLE001
            errors.append({"row": index, "message": str(exc)})

    if errors:
        return JSONResponse(status_code=422, content={"inserted": 0, "errors": errors})

    portfolio_id = await _default_portfolio_id(session)
    for payload in parsed:
        session.add(_build_transaction(payload, portfolio_id))
    await session.commit()
    return CsvImportResponse(inserted=len(parsed))
