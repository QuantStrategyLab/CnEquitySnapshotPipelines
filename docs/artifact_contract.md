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

---

# Artifact contract: cn_chinext_growth_momentum_quality_snapshot

Contract version: `cn_chinext_growth_momentum_quality_snapshot.factor_snapshot.v1`

## Required columns

| Column | Description |
| --- | --- |
| `symbol` | 6-digit ChiNext stock code, optional `.SZ` suffix in upstream feeds |
| `sector` | Industry sector label |
| `close_cny` | Latest close in CNY |
| `adv20_cny` | 20-day average daily value traded in CNY |
| `market_cap_cny` | Market capitalization in CNY |
| `revenue_yoy` | Revenue year-over-year growth |
| `profit_yoy` | Profit year-over-year growth |
| `revenue_acceleration_2q` | Two-quarter revenue acceleration |
| `roe_ttm` | Return on equity TTM |
| `roe_stability_3y` | ROE stability score (0-1) |
| `gross_margin_stability_3y` | Gross margin stability score (0-1) |
| `mom_12_1` | 12-1 month momentum |
| `mom_6_1` | 6-1 month momentum |
| `sma200_gap` | Close vs 200-day moving average gap |
| `realized_vol_126` | 126-day realized volatility |
| `earnings_positive` | Whether latest earnings are positive |
| `suspension_days_63` | Suspension days in last 63 sessions |
| `is_st` | ST/*ST flag |
| `list_days` | Listed trading days |

## A-share constraints

- Exclude `is_st=True` rows before ranking.
- Prefer `list_days >= 252` and `market_cap_cny >= 2e9` for production lineage.
- Treat this as a growth sleeve: use stronger liquidity and crowding checks than the dividend profile.
- Snapshot producer must be point-in-time safe; no future financial fields.

## Artifact pack

- `cn_chinext_growth_momentum_quality_snapshot_factor_snapshot_latest.csv`
- `cn_chinext_growth_momentum_quality_snapshot_factor_snapshot_latest.csv.manifest.json`
- `cn_chinext_growth_momentum_quality_snapshot_ranking_latest.csv`
- `release_status_summary.json`
