from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import pandas as pd

FACTOR_SNAPSHOT_COLUMNS = (
    "symbol",
    "sector",
    "close_cny",
    "adv20_cny",
    "market_cap_cny",
    "dividend_yield_ttm",
    "dividend_stability_3y",
    "earnings_positive",
    "payout_ratio",
    "roe_ttm",
    "roe_stability_3y",
    "realized_vol_126",
    "mom_12_1",
    "sma200_gap",
    "suspension_days_63",
    "is_st",
    "list_days",
)

FHPS_CANDIDATE_DATES = (
    "20241231",
    "20231231",
    "20221231",
    "20240630",
    "20230630",
)


def normalize_symbol(value: object) -> str:
    text = str(value or "").strip().upper()
    if text.endswith(".SH") or text.endswith(".SZ"):
        text = text.split(".", 1)[0]
    return text.zfill(6) if text.isdigit() else text


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        return default
    if pd.isna(resolved):
        return default
    return resolved


def compute_price_features(hist: pd.DataFrame) -> dict[str, float | int]:
    if hist.empty:
        raise ValueError("history must not be empty")
    frame = hist.sort_values("日期").copy()
    close = pd.to_numeric(frame["收盘"], errors="coerce")
    turnover = pd.to_numeric(frame["成交额"], errors="coerce")
    volume = pd.to_numeric(frame["成交量"], errors="coerce")
    if close.dropna().empty:
        raise ValueError("history close series is empty")

    latest_close = float(close.dropna().iloc[-1])
    adv20 = float(turnover.tail(20).mean()) if turnover.tail(20).notna().any() else 0.0
    returns = close.pct_change().dropna()
    if len(returns) >= 126:
        realized_vol_126 = float(returns.tail(126).std(ddof=0) * (252**0.5))
    else:
        realized_vol_126 = float(returns.std(ddof=0) * (252**0.5)) if len(returns) >= 2 else 0.18

    if len(close.dropna()) >= 252:
        mom_12_1 = float(close.iloc[-21] / close.iloc[-252] - 1.0)
    elif len(close.dropna()) >= 22:
        mom_12_1 = float(close.iloc[-1] / close.iloc[-22] - 1.0)
    else:
        mom_12_1 = 0.0

    if len(close.dropna()) >= 200:
        sma200_gap = float(latest_close / close.tail(200).mean() - 1.0)
    else:
        sma200_gap = 0.0

    suspension_days_63 = int((volume.tail(63).fillna(0) <= 0).sum())
    first_date = pd.to_datetime(frame["日期"].iloc[0], errors="coerce")
    list_days = 2000
    if pd.notna(first_date):
        list_days = max(int((pd.Timestamp(datetime.now(timezone.utc).date()) - first_date.normalize()).days), 1)

    return {
        "close_cny": latest_close,
        "adv20_cny": max(adv20, 1.0),
        "realized_vol_126": max(realized_vol_126, 0.01),
        "mom_12_1": mom_12_1,
        "sma200_gap": sma200_gap,
        "suspension_days_63": suspension_days_63,
        "list_days": list_days,
    }


def compute_dividend_stability(dividends: pd.DataFrame, *, years: int = 3) -> float:
    if dividends.empty or "派息" not in dividends.columns:
        return 0.0
    frame = dividends.copy()
    date_column = "除权除息日" if "除权除息日" in frame.columns else "股权登记日"
    frame["event_date"] = pd.to_datetime(frame[date_column], errors="coerce")
    frame["payout_per10"] = pd.to_numeric(frame["派息"], errors="coerce").fillna(0.0)
    frame = frame.loc[frame["payout_per10"] > 0.0].dropna(subset=["event_date"])
    if frame.empty:
        return 0.0

    latest_year = int(frame["event_date"].dt.year.max())
    recent = frame.loc[frame["event_date"].dt.year >= latest_year - years + 1]
    years_with_dividend = int(recent["event_date"].dt.year.nunique())
    coverage = years_with_dividend / float(years)
    payout_std = float(recent.groupby(recent["event_date"].dt.year)["payout_per10"].sum().std(ddof=0) or 0.0)
    payout_mean = float(recent.groupby(recent["event_date"].dt.year)["payout_per10"].sum().mean() or 0.0)
    variability = payout_std / payout_mean if payout_mean > 0 else 1.0
    consistency = max(0.0, 1.0 - min(variability, 1.0))
    return max(0.0, min(coverage * (0.5 + 0.5 * consistency), 1.0))


def compute_financial_features(financials: pd.DataFrame) -> dict[str, float | bool]:
    if financials.empty:
        return {
            "roe_ttm": 0.0,
            "roe_stability_3y": 0.0,
            "earnings_positive": False,
        }
    frame = financials.sort_values("日期").copy()
    roe = pd.to_numeric(frame.get("净资产报酬率(%)"), errors="coerce") / 100.0
    eps = pd.to_numeric(frame.get("摊薄每股收益(元)"), errors="coerce")
    roe = roe.dropna()
    eps = eps.dropna()
    latest_roe = float(roe.iloc[-1]) if not roe.empty else 0.0
    earnings_positive = bool(float(eps.iloc[-1]) > 0.0) if not eps.empty else False
    tail = roe.tail(12)
    if len(tail) >= 4 and abs(float(tail.mean())) > 1e-9:
        roe_stability_3y = max(0.0, 1.0 - min(float(tail.std(ddof=0) / abs(float(tail.mean()))), 1.0))
    else:
        roe_stability_3y = 0.5
    return {
        "roe_ttm": latest_roe,
        "roe_stability_3y": roe_stability_3y,
        "earnings_positive": earnings_positive,
    }


def extract_fhps_features(row: pd.Series, *, close_cny: float) -> dict[str, float | bool]:
    dividend_yield_ttm = _coerce_float(row.get("现金分红-股息率"), 0.0)
    cash_div_per10 = _coerce_float(row.get("现金分红-现金分红比例"), 0.0)
    eps = _coerce_float(row.get("每股收益"), 0.0)
    total_shares = _coerce_float(row.get("总股本"), 0.0)
    payout_ratio = 0.0
    if eps > 0 and cash_div_per10 > 0:
        payout_ratio = min((cash_div_per10 / 10.0) / eps, 2.0)
    market_cap_cny = total_shares * close_cny if total_shares > 0 and close_cny > 0 else 0.0
    name = str(row.get("名称") or "")
    return {
        "dividend_yield_ttm": max(dividend_yield_ttm, 0.0),
        "payout_ratio": payout_ratio,
        "market_cap_cny": market_cap_cny,
        "earnings_positive": eps > 0,
        "is_st": "ST" in name.upper(),
    }


def merge_factor_row(
    *,
    symbol: str,
    price: dict[str, float | int],
    fhps: dict[str, float | bool] | None,
    financials: dict[str, float | bool],
    dividend_stability_3y: float,
    sector: str,
) -> dict[str, object]:
    merged: dict[str, object] = {
        "symbol": normalize_symbol(symbol),
        "sector": sector or "unknown",
        "dividend_stability_3y": float(dividend_stability_3y),
        "suspension_days_63": int(price["suspension_days_63"]),
        "is_st": bool(fhps.get("is_st")) if fhps else False,
    }
    merged.update(price)
    merged.update(financials)
    if fhps:
        merged.update(fhps)
        if float(merged.get("market_cap_cny") or 0.0) <= 0:
            merged["market_cap_cny"] = float(price["close_cny"]) * 1_000_000_000.0
    else:
        merged.setdefault("dividend_yield_ttm", 0.0)
        merged.setdefault("payout_ratio", 0.0)
        merged.setdefault("market_cap_cny", float(price["close_cny"]) * 1_000_000_000.0)
    return {column: merged.get(column) for column in FACTOR_SNAPSHOT_COLUMNS}


def stamp_as_of(frame: pd.DataFrame, *, as_of: str | date | None = None) -> pd.DataFrame:
    output = frame.copy()
    if "as_of" in output.columns or "snapshot_date" in output.columns:
        return output
    stamp = as_of or datetime.now(timezone.utc).date().isoformat()
    output.insert(0, "as_of", str(stamp))
    return output
