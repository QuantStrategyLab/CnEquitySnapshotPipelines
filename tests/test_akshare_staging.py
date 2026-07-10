from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from cn_equity_snapshot_pipelines.akshare_market_history import build_market_history_frame, normalize_symbol
from cn_equity_snapshot_pipelines.akshare_staging import build_factor_row_from_akshare, build_factor_snapshot_from_akshare


def test_resolve_universe_symbols_expanded_from_fhps():
    from cn_equity_snapshot_pipelines.akshare_staging import resolve_universe_symbols

    fhps = pd.DataFrame(
        [
            {"代码": "601088", "名称": "中国神华", "现金分红-股息率": 0.06},
            {"代码": "600519", "名称": "贵州茅台", "现金分红-股息率": 0.05},
            {"代码": "601398", "名称": "工商银行", "现金分红-股息率": 0.015},
        ]
    )
    symbols = resolve_universe_symbols(object(), fhps, mode="expanded", expanded_top_n=2)
    assert symbols == ("601088", "600519")


def test_build_factor_snapshot_falls_back_to_sample():
    sample_path = Path(__file__).resolve().parents[1] / "examples" / "dividend_quality" / "factor_snapshot.sample.csv"
    frame, diagnostics = build_factor_snapshot_from_akshare(
        symbols=("999999",),
        sample_fallback_path=sample_path,
        min_rows=1,
        as_of="2026-06-27",
        sector_map={},
    )
    assert diagnostics["source"] == "sample_fallback"
    assert "as_of" in frame.columns
    assert len(frame) >= 1


def test_build_factor_row_from_mocked_akshare_sources():
    history = pd.DataFrame(
        {
            "日期": pd.bdate_range("2024-01-02", periods=260),
            "收盘": [100 + idx * 0.1 for idx in range(260)],
            "成交额": [80_000_000.0] * 260,
            "成交量": [900_000] * 260,
        }
    )
    financials = pd.DataFrame(
        {
            "日期": ["2025-03-31", "2025-06-30"],
            "净资产报酬率(%)": [10.0, 11.0],
            "摊薄每股收益(元)": [2.0, 2.2],
        }
    )
    dividends = pd.DataFrame({"除权除息日": ["2025-06-20"], "派息": [250.0]})
    fhps = pd.DataFrame(
        [
            {
                "代码": "600519",
                "symbol": "600519",
                "名称": "贵州茅台",
                "现金分红-股息率": 0.028,
                "现金分红-现金分红比例": 250.0,
                "每股收益": 50.0,
                "总股本": 1_000_000_000,
            }
        ]
    )

    row = build_factor_row_from_akshare(
        "600519",
        fhps_table=fhps,
        fetch_history=lambda _symbol: history,
        fetch_financials=lambda _symbol: financials,
        fetch_dividends=lambda _symbol: dividends,
        fetch_sector=lambda _symbol: "白酒",
    )
    assert row["symbol"] == "600519"
    assert row["sector"] == "白酒"
    assert float(row["dividend_yield_ttm"]) == pytest.approx(0.028)
    assert float(row["roe_ttm"]) == pytest.approx(0.11)
    assert float(row["realized_vol_126"]) > 0


def test_build_market_history_frame_from_mocked_fetchers():
    def _fetch(symbol: str) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "日期": pd.bdate_range("2024-01-02", periods=3),
                "收盘": [10.0, 10.1, 10.2],
            }
        )

    from cn_equity_snapshot_pipelines import akshare_market_history as module

    original = module.fetch_etf_history
    module.fetch_etf_history = lambda symbol, **kwargs: pd.DataFrame(
        {
            "date": ["2024-01-02", "2024-01-03", "2024-01-04"],
            "symbol": normalize_symbol(symbol),
            "close": [10.0, 10.1, 10.2],
        }
    )
    try:
        frame = build_market_history_frame(("510300", "510500"), ak=object(), request_delay_seconds=0)
        assert set(frame["symbol"]) == {"510300", "510500"}
        assert len(frame) == 6
    finally:
        module.fetch_etf_history = original


def test_build_market_history_frame_rejects_partial_history(monkeypatch: pytest.MonkeyPatch):
    from cn_equity_snapshot_pipelines import akshare_market_history as module

    def _fetch(symbol: str, **kwargs):
        if symbol == "510500":
            raise ConnectionError("unavailable")
        return pd.DataFrame({"date": ["2024-01-02"], "symbol": [symbol], "close": [10.0]})

    monkeypatch.setattr(module, "fetch_etf_history", _fetch)

    with pytest.raises(RuntimeError, match="510500"):
        build_market_history_frame(("510300", "510500"), ak=object(), request_delay_seconds=0)


def test_build_market_history_frame_supports_yahoo_source(monkeypatch: pytest.MonkeyPatch):
    from cn_equity_snapshot_pipelines import akshare_market_history as module

    monkeypatch.setattr(
        module,
        "fetch_yahoo_etf_history",
        lambda symbol, **kwargs: pd.DataFrame(
            {"date": ["2024-01-02"], "symbol": [symbol], "close": [10.0]}
        ),
    )

    frame = build_market_history_frame(
        ("510300", "159915"),
        source="yahoo",
        request_delay_seconds=0,
    )

    assert set(frame["symbol"]) == {"510300", "159915"}
    assert module.yahoo_symbol("510300") == "510300.SS"
    assert module.yahoo_symbol("159915") == "159915.SZ"
