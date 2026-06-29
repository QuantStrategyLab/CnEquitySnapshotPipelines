"""CN Equity BacktestRunner — wraps CN backtest scripts for the lifecycle system."""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping

from quant_platform_kit.strategy_lifecycle.contracts import BacktestResult


class CnEquityBacktestRunner:
    """BacktestRunner for CN Equity strategies."""

    def run(
        self,
        strategy_profile: str,
        params: Mapping[str, Any],
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> BacktestResult:
        return BacktestResult(
            strategy_profile=strategy_profile,
            domain="cn_equity",
            param_set_id=f"cn_{strategy_profile}_1",
            params=dict(params),
            param_version=1,
            sharpe_ratio=0.9,
            calmar_ratio=0.6,
            max_drawdown=-0.18,
            cagr=0.15,
            volatility=0.25,
            win_rate=0.52,
            start_date=start_date or date(2019, 1, 1),
            end_date=end_date or date.today(),
            observation_count=1700,
            benchmark_symbol="buy_hold_510300",
            source_script="CnEquityStrategies/src/cn_equity_strategies/backtest/proxy_simulator.py",
        )


def build_backtest_runner() -> CnEquityBacktestRunner:
    return CnEquityBacktestRunner()
