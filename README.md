# CnEquitySnapshotPipelines

Feature-snapshot pipeline that builds validated A-share factor snapshots and evidence artifacts for QuantStrategyLab strategy runtimes.

English | [简体中文](README.zh-CN.md)

## Status

Experimental. Use at your own risk. This project is not financial advice and
does not guarantee any trading result. Its outputs are research evidence, not
trading instructions.

## What this is

CnEquitySnapshotPipelines builds factor-snapshot artifacts, manifests, ranking
previews, and release summaries for snapshot-backed A-share strategy runtimes.
It reads factor input CSVs, or stages them from public data with AkShare, and
emits a versioned artifact pack under a stable contract. Downstream strategy and
execution repositories consume only validated artifacts.

## What this is not

It does not place broker orders. It does not store broker credentials. It does
not make a strategy live on its own.

## Quick start

Requires Python 3.11 or newer.

```bash
python -m pip install -e '.[test]'
python -m pytest -q
```

Build the sample artifact packs from the bundled example inputs:

```bash
python scripts/build_dividend_quality_sample.py
python scripts/build_chinext_growth_momentum_quality_sample.py
```

Or run an installed builder:

```bash
cneq-build-dividend-quality-snapshot \
  --factor-snapshot examples/dividend_quality/factor_snapshot.sample.csv \
  --output-dir data/output/dividend_quality
```

To stage factor inputs from public data, install the optional extra:

```bash
python -m pip install -e '.[public-data]'
cneq-stage-akshare-dividend-quality --output data/staging/dividend_quality/factor_snapshot.latest.csv
```

## Configuration

The builders take command-line flags rather than environment variables.

| Flag | Required | Default | Purpose |
|---|---|---|---|
| `--factor-snapshot` | Yes | none | Factor input CSV; the expected columns are in [`docs/artifact-contract.md`](docs/artifact-contract.md). |
| `--output-dir` | No | `data/output/<profile>` | Directory for the generated artifact pack. |
| `--min-adv20-cny` | No | `0.0` | Minimum 20-day average traded value filter. |
| `--min-market-cap-cny` | No | `0.0` | Minimum market-cap filter. |
| `--as-of` | No | UTC today | Snapshot date in `YYYY-MM-DD` form. |
| `--output` | No | `data/staging/<profile>/factor_snapshot.latest.csv` | Output path for the AkShare staging commands. |

## Repository layout

```
src/        snapshot builders, contracts, and lifecycle helpers
scripts/    sample-build scripts
examples/   sample factor inputs
docs/       artifact contract and design notes
tests/      unit and contract tests
data/       local working output
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). AI assistants and maintainers also follow [AGENTS.md](AGENTS.md).

## Security

See [SECURITY.md](SECURITY.md). Do not open a public issue for a vulnerability.
Keep credentials, private data, and external service tokens out of Git and logs.

## License

MIT. See [LICENSE](LICENSE).
