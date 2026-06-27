from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from cn_equity_snapshot_pipelines.akshare_metadata import (
    build_symbol_sector_map,
    list_days_from_history,
    lookup_sector,
    select_dividend_universe_symbols,
)


def test_select_dividend_universe_symbols_filters_and_ranks():
    fhps = pd.DataFrame(
        [
            {"代码": "600519", "名称": "贵州茅台", "现金分红-股息率": 0.05},
            {"代码": "000001", "名称": "平安银行", "现金分红-股息率": 0.04},
            {"代码": "600001", "名称": "*ST测试", "现金分红-股息率": 0.08},
            {"代码": "601398", "名称": "工商银行", "现金分红-股息率": 0.015},
            {"代码": "601088", "名称": "中国神华", "现金分红-股息率": 0.06},
        ]
    )
    selected = select_dividend_universe_symbols(fhps, top_n=2)
    assert selected == ("601088", "600519")


def test_lookup_sector_from_map():
    sector_map = {"600519": "白酒", "601088": "煤炭行业"}
    assert lookup_sector("600519", sector_map) == "白酒"
    assert lookup_sector("999999", sector_map) == "unknown"
    assert lookup_sector("600519", None) == "unknown"


def test_build_symbol_sector_map_returns_empty_without_cache_or_refresh(tmp_path: Path):
    cache_path = tmp_path / "missing.json"

    class FakeAk:
        def stock_board_industry_name_em(self):
            raise AssertionError("should not fetch boards without force_refresh")

    mapping = build_symbol_sector_map(FakeAk(), cache_path=cache_path)
    assert mapping == {}


def test_build_symbol_sector_map_uses_cache(tmp_path: Path):
    cache_path = tmp_path / "symbol_sector_map.json"
    cache_path.write_text(
        json.dumps({"600519": "白酒", "601088": "煤炭行业"}, ensure_ascii=False),
        encoding="utf-8",
    )

    class FakeAk:
        def stock_board_industry_name_em(self):
            raise AssertionError("should not fetch boards when cache exists")

    mapping = build_symbol_sector_map(FakeAk(), cache_path=cache_path)
    assert mapping["600519"] == "白酒"
    assert mapping["601088"] == "煤炭行业"


def test_build_symbol_sector_map_builds_and_writes_cache(tmp_path: Path):
    cache_path = tmp_path / "symbol_sector_map.json"

    class FakeAk:
        def stock_board_industry_name_em(self):
            return pd.DataFrame({"板块名称": ["白酒", "煤炭行业"]})

        def stock_board_industry_cons_em(self, symbol: str):
            if symbol == "白酒":
                return pd.DataFrame({"代码": ["600519", "000858"]})
            if symbol == "煤炭行业":
                return pd.DataFrame({"代码": ["601088"]})
            return pd.DataFrame()

    mapping = build_symbol_sector_map(
        FakeAk(),
        cache_path=cache_path,
        sleep_seconds=0.0,
        force_refresh=True,
    )
    assert mapping["600519"] == "白酒"
    assert mapping["601088"] == "煤炭行业"
    assert cache_path.exists()


def test_list_days_from_history_respects_as_of():
    hist = pd.DataFrame({"日期": pd.bdate_range("2024-01-02", periods=10)})
    days = list_days_from_history(hist, as_of=pd.Timestamp("2024-01-12").date())
    assert days == pytest.approx(10, abs=2)
