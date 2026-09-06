"""Unsupported CN Equity backtest adapter for the lifecycle system."""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping

from quant_platform_kit.strategy_lifecycle.contracts import BacktestResult


class CnEquityBacktestRunner:
    """Retain the runner interface without fabricating backtest results."""

    def run(
        self,
        strategy_profile: str,
        params: Mapping[str, Any],
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> BacktestResult:
        raise NotImplementedError(
            "CN equity backtesting is unsupported: no real lifecycle backtest runner is implemented"
        )


def build_backtest_runner() -> CnEquityBacktestRunner:
    return CnEquityBacktestRunner()
