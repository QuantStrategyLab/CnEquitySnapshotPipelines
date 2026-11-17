# Index ETF input for strict backtests

[简体中文](index-etf-execution-input.zh-CN.md)

`cneq-publish-index-etf-input` packages licensed local JSON exports as immutable inputs for strict daily-bar backtests in CnEquityStrategies. The import runs offline and preserves gaps in the source data. The existing industry ETF and Tencent collection entry points are separate.

Provide unadjusted trade-price OHLC, volume in ETF units, the trading calendar, suspension and price-limit states known before each session opens, and dividend and split events covering the entire window. Dividends require a record date, ex-dividend date, payment date and applicable net distribution. The import stops if fields or sessions are missing, rows are duplicated, or event coverage cannot be proved. The publisher uses the strategy repository's `load_index_etf_input` to validate fields and timing.

The package keeps the three original JSON files byte for byte, alongside the normalized members. It records the license text, source, version and scope of permitted private retention. Use the observation time recorded in the export. Preserve missing dates, trading-calendar entries and event records as gaps that stop the import.

Publishing requires absolute paths without symbolic links, a clean and identifiable source revision, and two existing private storage directories outside the repository on separate devices. The publisher validates and syncs files before publishing them atomically and reading them back. Identical inputs reuse the existing package. A package with the same name and different bytes is rejected. If the second copy fails, the command reports failure and retains the completed first copy. Retry with the original inputs after fixing the storage problem.

`--evidence-kind` defaults to `synthetic`. The `historical` value records the operator's source claim. The owning job independently verifies the license, source and retention rights before binding the same manifest root in protected configuration. Synthetic inputs are for tests. Trading and promotion require separate authorization.

Use `--help` for required paths and source fields. Output consists of the publication status, manifest root and `promotion_authority_granted=false`.

Offline tests use synthetic data, simulate two devices, and read the published artifact through the actual strategy consumer. These tests verify the artifact bytes and consumer interface. Historical source coverage, backtest results and shadow validation require separate evidence.

The recorded verification used the CnEquityStrategies and QuantPlatformKit revisions pinned in the lockfile. A fresh environment installed both packages from the lockfile and confirmed their revisions and `site-packages` locations with `PYTHONPATH` cleared. All 145 tests passed offline, 12 installed dependencies were compatible, and Ruff, diff checks, wheel and source distribution builds passed. Independent review confirmed that the CN reader accepted the artifact and that the first copy survived a second-copy failure and could be completed on retry. The run used synthetic inputs and produced no research candidate.
