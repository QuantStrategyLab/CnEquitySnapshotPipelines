from __future__ import annotations

import pandas as pd
from cn_equity_snapshot_pipelines.akshare_enrichment import (
    compute_dividend_stability,
    compute_financial_features,
    compute_price_features,
    extract_fhps_features,
    merge_factor_row,
)


def _sample_history(rows: int = 280) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-02", periods=rows)
    close = [100 + idx * 0.05 for idx in range(rows)]
    return pd.DataFrame(
        {
            "日期": dates,
            "收盘": close,
            "成交额": [50_000_000.0 + idx * 1000 for idx in range(rows)],
            "成交量": [1_000_000 + idx for idx in range(rows)],
        }
    )


def test_compute_price_features_from_history():
    features = compute_price_features(_sample_history())
    assert features["close_cny"] > 100
    assert features["adv20_cny"] > 0
    assert features["realized_vol_126"] > 0
    assert features["list_days"] > 200


def test_compute_price_features_list_days_uses_as_of():
    hist = _sample_history(rows=30)
    as_of = pd.Timestamp("2024-03-01").date()
    features = compute_price_features(hist, as_of=as_of)
    full_features = compute_price_features(hist)
    assert features["list_days"] <= full_features["list_days"]
    assert features["list_days"] > 0


def test_compute_financial_features_uses_latest_roe():
    financials = pd.DataFrame(
        {
            "日期": ["2024-03-31", "2024-06-30", "2024-09-30"],
            "净资产报酬率(%)": [8.0, 9.0, 10.0],
            "摊薄每股收益(元)": [1.0, 1.1, 1.2],
        }
    )
    features = compute_financial_features(financials)
    assert features["roe_ttm"] == 0.10
    assert features["earnings_positive"] is True
    assert 0.0 <= features["roe_stability_3y"] <= 1.0


def test_compute_dividend_stability_from_history():
    dividends = pd.DataFrame(
        {
            "除权除息日": ["2024-06-20", "2025-06-20", "2026-06-20"],
            "派息": [200.0, 210.0, 220.0],
        }
    )
    stability = compute_dividend_stability(dividends, years=3)
    assert stability >= 0.8


def test_merge_factor_row_prefers_fhps_dividend_fields():
    fhps = pd.Series(
        {
            "名称": "贵州茅台",
            "现金分红-股息率": 0.03,
            "现金分红-现金分红比例": 276.0,
            "每股收益": 68.0,
            "总股本": 1_256_197_800,
        }
    )
    row = merge_factor_row(
        symbol="600519",
        price={
            "close_cny": 1000.0,
            "adv20_cny": 100_000_000.0,
            "realized_vol_126": 0.2,
            "mom_12_1": 0.05,
            "sma200_gap": 0.03,
            "suspension_days_63": 0,
            "list_days": 3000,
        },
        fhps=extract_fhps_features(fhps, close_cny=1000.0),
        financials={"roe_ttm": 0.12, "roe_stability_3y": 0.7, "earnings_positive": True},
        dividend_stability_3y=0.8,
        sector="白酒",
    )
    assert row["dividend_yield_ttm"] == 0.03
    assert row["sector"] == "白酒"
    assert float(row["market_cap_cny"]) > 0
