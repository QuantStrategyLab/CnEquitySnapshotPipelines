from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from cn_equity_snapshot_pipelines.akshare_market_history import build_market_history_frame, normalize_symbol
from cn_equity_snapshot_pipelines.akshare_staging import build_factor_row_from_akshare, build_factor_snapshot_from_akshare
from cn_equity_snapshot_pipelines import akshare_staging as staging


@pytest.fixture(autouse=True)
def frozen_staging_clock(monkeypatch):
    clock = Mock(return_value=datetime(2026, 6, 27, 23, 59, tzinfo=timezone.utc))
    monkeypatch.setattr(staging, "datetime", SimpleNamespace(now=clock))
    return clock


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


def test_build_factor_snapshot_falls_back_to_sample(monkeypatch):
    monkeypatch.setattr(staging, "_import_akshare", Mock(side_effect=RuntimeError("synthetic unavailable")))
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


@pytest.mark.parametrize("as_of", ["2026-06-26", "2026-06-28", "invalid", "", "2026-02-30"])
@pytest.mark.parametrize("entrypoint", ["builder", "writer", "cli"])
def test_current_only_rejects_date_before_adapter_or_output(monkeypatch, tmp_path, as_of, entrypoint):
    importer = Mock(side_effect=RuntimeError("synthetic unavailable"))
    monkeypatch.setattr(staging, "_import_akshare", importer)
    sample = Path(__file__).resolve().parents[1] / "examples/dividend_quality/factor_snapshot.sample.csv"
    output = tmp_path / "not-created" / "snapshot.csv"

    with pytest.raises(ValueError, match="current-only.*UTC"):
        if entrypoint == "builder":
            staging.build_factor_snapshot_from_akshare(as_of=as_of, sample_fallback_path=sample)
        elif entrypoint == "writer":
            staging.write_staging_factor_snapshot(output_path=output, as_of=as_of, sample_fallback_path=sample)
        else:
            staging.main(["--output", str(output), "--as-of", as_of, "--sample-fallback", str(sample)])

    importer.assert_not_called()
    assert not output.parent.exists()


@pytest.mark.parametrize("as_of", [None, "2026-06-27"])
def test_current_snapshot_keeps_one_utc_date_across_midnight(monkeypatch, frozen_staging_clock, as_of):
    def fhps_table(**kwargs):
        frozen_staging_clock.return_value = datetime(2026, 6, 28, tzinfo=timezone.utc)
        return pd.DataFrame({"代码": ["000001"]})

    ak = SimpleNamespace(
        stock_fhps_em=Mock(side_effect=fhps_table),
        stock_zh_a_hist=Mock(return_value=pd.DataFrame({
            "日期": ["2026-06-25", "2026-06-26", "2026-06-28"],
            "收盘": [10.0, 11.0, 999.0], "成交额": [100.0, 200.0, 900.0], "成交量": [1, 1, 1],
        })),
        stock_financial_analysis_indicator=Mock(return_value=pd.DataFrame({
            "日期": ["2026-03-31"], "净资产报酬率(%)": [10.0], "摊薄每股收益(元)": [1.0],
        })),
        stock_history_dividend_detail=Mock(return_value=pd.DataFrame()),
    )
    monkeypatch.setattr(staging, "_import_akshare", Mock(return_value=ak))
    frame, diagnostics = staging.build_factor_snapshot_from_akshare(
        symbols=("000001",), min_rows=1, as_of=as_of, sector_map={},
    )

    assert diagnostics["source"] == "akshare"
    assert list(frame["as_of"]) == ["2026-06-27"]  # Saturday is a valid acquisition date.
    assert list(frame["close_cny"]) == [11.0]  # Prior-session bars remain valid inputs.
    assert ak.stock_zh_a_hist.call_args.kwargs["end_date"] == "20260627"
    frozen_staging_clock.assert_called_once_with(timezone.utc)


@pytest.mark.parametrize("date_column", ["as_of", "snapshot_date"])
def test_sample_fallback_preserves_original_fixture_date(monkeypatch, date_column):
    sample = pd.DataFrame([{column: 0 for column in staging.FACTOR_SNAPSHOT_COLUMNS}])
    sample.insert(0, date_column, "2020-01-02")
    monkeypatch.setattr(staging.pd, "read_csv", lambda path: sample.copy())
    monkeypatch.setattr(staging, "_import_akshare", Mock(side_effect=RuntimeError("synthetic unavailable")))
    frame, diagnostics = staging.build_factor_snapshot_from_akshare(sample_fallback_path="synthetic.csv")

    assert diagnostics["source"] == "sample_fallback"
    assert list(frame[date_column]) == ["2020-01-02"]


def test_writer_rejects_sample_before_creating_output(monkeypatch, tmp_path):
    monkeypatch.setattr(staging, "_import_akshare", Mock(side_effect=RuntimeError("synthetic unavailable")))
    sample = Path(__file__).resolve().parents[1] / "examples/dividend_quality/factor_snapshot.sample.csv"
    output = tmp_path / "not-created" / "snapshot.csv"
    with pytest.raises(ValueError, match="sample fallback.*cannot be written"):
        staging.write_staging_factor_snapshot(output_path=output, sample_fallback_path=sample)
    assert not output.parent.exists()


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
    assert module.tencent_symbol("510300") == "sh510300"
    assert module.tencent_symbol("159915") == "sz159915"


def test_yahoo_history_preserves_adjusted_close_contract(monkeypatch: pytest.MonkeyPatch):
    from cn_equity_snapshot_pipelines import akshare_market_history as module

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "chart": {
                    "result": [
                        {
                            "timestamp": [1704153600],
                            "indicators": {
                                "quote": [{"close": [12.0]}],
                                "adjclose": [{"adjclose": [10.0]}],
                            },
                        }
                    ]
                }
            }

    monkeypatch.setitem(sys.modules, "requests", SimpleNamespace(get=lambda *args, **kwargs: _Response()))

    frame = module.fetch_yahoo_etf_history("510300", start_date="20240102", end_date="20240102")

    assert frame.iloc[0]["close"] == 10.0
    assert module.PRICE_BASIS == "adjusted_close_equivalent"


def test_yahoo_history_rejects_incomplete_adjusted_series(monkeypatch: pytest.MonkeyPatch):
    from cn_equity_snapshot_pipelines import akshare_market_history as module

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "chart": {
                    "result": [
                        {
                            "timestamp": [1704153600, 1704240000],
                            "indicators": {
                                "quote": [{"close": [12.0, 13.0]}],
                                "adjclose": [{"adjclose": [10.0]}],
                            },
                        }
                    ]
                }
            }

    monkeypatch.setitem(sys.modules, "requests", SimpleNamespace(get=lambda *args, **kwargs: _Response()))

    with pytest.raises(ValueError, match="incomplete Yahoo adjusted ETF history"):
        module.fetch_yahoo_etf_history(
            "510300", start_date="20240101", end_date="20240103", max_attempts=1
        )


def test_tencent_history_labels_identity_adjustment(monkeypatch: pytest.MonkeyPatch):
    from cn_equity_snapshot_pipelines import akshare_market_history as module

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"data": {"sh510300": {"day": [["2024-01-02", "10", "11"]]}}}

    monkeypatch.setitem(sys.modules, "requests", SimpleNamespace(get=lambda *args, **kwargs: _Response()))

    frame = module.fetch_tencent_etf_history(
        "510300", start_date="20240102", end_date="20240102", max_attempts=1
    )

    assert set(frame["price_basis"]) == {"tencent_qfq_identity"}


def test_tencent_history_skips_pre_inception_chunks(monkeypatch: pytest.MonkeyPatch):
    from cn_equity_snapshot_pipelines import akshare_market_history as module

    class _Response:
        def __init__(self, params: dict[str, str]) -> None:
            self.params = params

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            if "2020-01-01" in self.params["param"]:
                return {"data": {"sh510300": {"qfqday": []}}}
            dates = pd.bdate_range("2022-06-01", "2023-12-29")
            rows = [[date.date().isoformat(), "10", "11"] for date in dates]
            return {"data": {"sh510300": {"qfqday": rows}}}

    monkeypatch.setitem(
        sys.modules,
        "requests",
        SimpleNamespace(get=lambda *args, **kwargs: _Response(kwargs["params"])),
    )

    frame = module.fetch_tencent_etf_history(
        "510300", start_date="20200101", end_date="20231231", max_attempts=1
    )

    assert frame["date"].min() == "2022-06-01"
    assert frame["date"].max() == "2023-12-29"


def test_history_coverage_rejects_truncated_series() -> None:
    from cn_equity_snapshot_pipelines import akshare_market_history as module

    frame = pd.DataFrame(
        {"date": pd.bdate_range("2024-06-01", "2024-12-31"), "symbol": "510300", "close": 10.0}
    )

    with pytest.raises(ValueError, match="incomplete adjusted ETF history coverage"):
        module._validate_history_coverage(
            frame,
            symbol="510300",
            start_date=pd.Timestamp("2024-01-01"),
            end_date=pd.Timestamp("2024-12-31"),
        )
