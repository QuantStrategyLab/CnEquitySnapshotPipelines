# CnEquitySnapshotPipelines


## QSL architecture role

- **Layer**: `pipeline`.
- **Responsibility**: A-share snapshot and evidence pipeline.
- **Owns**: validated factor snapshots, manifests, ranking previews, release evidence.
- **Consumes**: CnEquityStrategies metadata and upstream market inputs.
- **Must not**: place broker orders or decide live enablement alone.

[Chinese README](README.zh-CN.md)

> Investing involves risk. This project does not provide investment advice and is for education, research, and engineering review only.

## What this repository is

`CnEquitySnapshotPipelines` builds feature-snapshot artifacts, manifests, ranking previews, and release summaries for snapshot-backed A-share strategy runtimes in QuantStrategyLab.

This repository produces evidence and artifacts. It does not place broker orders, store broker credentials, or make a strategy live by itself.

## Active snapshot profile

| Profile | Display name | Contract | Builder |
| --- | --- | --- | --- |
| `cn_dividend_quality_snapshot` | CN Dividend Quality Snapshot | `cn_dividend_quality_snapshot.factor_snapshot.v1` | `cneq-build-dividend-quality-snapshot` |
| `cn_chinext_growth_momentum_quality_snapshot` | CN ChiNext Growth Momentum Quality Snapshot | `cn_chinext_growth_momentum_quality_snapshot.factor_snapshot.v1` | `cneq-build-chinext-growth-momentum-quality-snapshot` |

## Quick start

```bash
python -m pip install -e '.[test]'
python -m pytest -q
```

Build a sample artifact pack locally:

```bash
PYTHONPATH=src python scripts/build_dividend_quality_sample.py
PYTHONPATH=src python scripts/build_chinext_growth_momentum_quality_sample.py
```

Stage real factor inputs via AkShare (falls back to sample CSV on failure):

```bash
cneq-stage-akshare-dividend-quality --output data/staging/dividend_quality/factor_snapshot.latest.csv
cneq-stage-akshare-market-history --output data/staging/market_history/etf_universe.latest.csv
```

Or use the installed entrypoint:

```bash
cneq-build-dividend-quality-snapshot \
  --factor-snapshot examples/dividend_quality/factor_snapshot.sample.csv \
  --output-dir data/output/dividend_quality

cneq-build-chinext-growth-momentum-quality-snapshot \
  --factor-snapshot examples/chinext_growth_momentum_quality/factor_snapshot.sample.csv \
  --output-dir data/output/chinext_growth_momentum_quality
```

## Downstream use

`CnEquityStrategies` and future `QmtPlatform` should consume only validated artifacts for runtime-enabled profiles.

See [`docs/artifact_contract.md`](docs/artifact_contract.md) for the factor snapshot schema.

## License

See [LICENSE](LICENSE).
