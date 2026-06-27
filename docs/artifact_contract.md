# Artifact contract: cn_dividend_quality_snapshot

Contract version: `cn_dividend_quality_snapshot.factor_snapshot.v1`

## Required columns

| Column | Description |
| --- | --- |
| `symbol` | 6-digit A-share code, optional `.SH`/`.SZ` suffix in upstream feeds |
| `sector` | Industry sector label |
| `close_cny` | Latest close in CNY |
| `adv20_cny` | 20-day average daily value traded in CNY |
| `market_cap_cny` | Market capitalization in CNY |
| `dividend_yield_ttm` | Trailing twelve-month dividend yield |
| `dividend_stability_3y` | Dividend stability score (0-1) |
| `earnings_positive` | Whether latest earnings are positive |
| `payout_ratio` | Dividend payout ratio |
| `roe_ttm` | Return on equity TTM |
| `roe_stability_3y` | ROE stability score (0-1) |
| `realized_vol_126` | 126-day realized volatility |
| `mom_12_1` | 12-1 month momentum |
| `sma200_gap` | Close vs 200-day moving average gap |
| `suspension_days_63` | Suspension days in last 63 sessions |
| `is_st` | ST/*ST flag |
| `list_days` | Listed trading days |

## A-share constraints

- Exclude `is_st=True` rows before ranking.
- Prefer `list_days >= 252` and `market_cap_cny >= 5e9` for production lineage.
- Snapshot producer must be point-in-time safe; no future financial fields.

## Artifact pack

- `cn_dividend_quality_snapshot_factor_snapshot_latest.csv`
- `cn_dividend_quality_snapshot_factor_snapshot_latest.csv.manifest.json`
- `cn_dividend_quality_snapshot_ranking_latest.csv`
- `release_status_summary.json`
