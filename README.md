# CnEquitySnapshotPipelines

[Chinese README](README.zh-CN.md)

> Investing involves risk. This project does not provide investment advice and is for education, research, and engineering review only.

## What this repository is

`CnEquitySnapshotPipelines` builds feature-snapshot artifacts, manifests, ranking previews, and release summaries for snapshot-backed A-share strategy runtimes in QuantStrategyLab.

This repository produces evidence and artifacts. It does not place broker orders, store broker credentials, or make a strategy live by itself.

## Active snapshot profile

| Profile | Display name | Contract | Builder |
| --- | --- | --- | --- |
| `cn_dividend_quality_snapshot` | CN Dividend Quality Snapshot | `cn_dividend_quality_snapshot.factor_snapshot.v1` | `cneq-build-dividend-quality-snapshot` |

## Quick start

```bash
python -m pip install -e '.[test]'
python -m pytest -q
```

Build a sample artifact pack locally:

```bash
PYTHONPATH=src python scripts/build_dividend_quality_sample.py
```

Or use the installed entrypoint:

```bash
cneq-build-dividend-quality-snapshot \
  --factor-snapshot examples/dividend_quality/factor_snapshot.sample.csv \
  --output-dir data/output/dividend_quality
```

## Downstream use

`CnEquityStrategies` and future `QmtPlatform` should consume only validated artifacts for runtime-enabled profiles.

See [`docs/artifact_contract.md`](docs/artifact_contract.md) for the factor snapshot schema.

## License

See [LICENSE](LICENSE).
