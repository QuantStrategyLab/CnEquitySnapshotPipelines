"""CN Equity return matrix adapter."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from quant_platform_kit.strategy_lifecycle.performance_metrics import normalize_return_matrix


def read_cn_equity_returns(
    artifact_root: str | Path | None = None,
    *,
    date_column: str = "as_of",
) -> pd.DataFrame:
    if artifact_root is None:
        artifact_root = Path(__file__).resolve().parents[3] / "data" / "output"
    root = Path(artifact_root)
    paths = sorted(root.rglob("portfolio_and_tracker_returns.csv"))
    if not paths:
        raise FileNotFoundError(f"No return matrix found under {root}")
    frame = pd.read_csv(str(paths[-1]))
    return normalize_return_matrix(frame, date_column=date_column)
