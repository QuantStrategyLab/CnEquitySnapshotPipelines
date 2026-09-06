from __future__ import annotations

from datetime import date
from unittest.mock import Mock

import pytest

from quant_platform_kit.strategy_lifecycle.backtest_orchestrator import BacktestOrchestrator
from quant_platform_kit.strategy_lifecycle.contracts import PromotionCostModel, PurgedWalkForwardFold
from quant_platform_kit.strategy_lifecycle.performance_store import PerformanceStore
from strategy_lifecycle.backtest_wrapper import build_backtest_runner


@pytest.mark.parametrize(
    ("profile", "params", "start_date", "end_date"),
    [
        ("missing_profile", {}, None, None),
        ("cn_dividend_quality", {}, date(2024, 1, 2), date(2024, 1, 2)),
        ("cn_dividend_quality", {"lookback": 20}, date(2019, 1, 1), date(2024, 1, 2)),
    ],
)
def test_unimplemented_runner_rejects_instead_of_returning_fixed_metrics(profile, params, start_date, end_date):
    with pytest.raises(NotImplementedError, match="CN equity backtesting is unsupported"):
        build_backtest_runner().run(profile, params, start_date=start_date, end_date=end_date)


def test_orchestrator_propagates_unsupported_without_persisting_result():
    store = Mock(spec=PerformanceStore)
    orchestrator = BacktestOrchestrator(store=store)
    orchestrator.register_runner("cn_equity", build_backtest_runner())

    with pytest.raises(NotImplementedError, match="CN equity backtesting is unsupported"):
        orchestrator.run(
            "missing_profile",
            domain="cn_equity",
            params={},
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 2),
        )

    store.save_backtest_result.assert_not_called()


def test_promotion_still_rejects_placeholder_without_persisting_result():
    store = Mock(spec=PerformanceStore)
    orchestrator = BacktestOrchestrator(store=store)
    orchestrator.register_runner("cn_equity", build_backtest_runner())
    folds = tuple(
        PurgedWalkForwardFold(
            train_start=date(year, 1, 1),
            train_end=date(year, 6, 1),
            test_start=date(year, 7, 1),
            test_end=date(year, 12, 1),
        )
        for year in (2019, 2020, 2021)
    )

    with pytest.raises(RuntimeError, match="requires explicit runner_kind='real'"):
        orchestrator.run_promotion(
            "missing_profile",
            domain="cn_equity",
            params={},
            folds=folds,
            locked_oos_start=date(2022, 1, 1),
            locked_oos_end=date(2023, 1, 1),
            purge_days=1,
            embargo_days=1,
            source_revision="a" * 40,
            cost_model=PromotionCostModel(
                model_id="synthetic_test", commission_bps=1, slippage_bps=1, market_impact_bps=0
            ),
        )

    store.save_backtest_result.assert_not_called()
