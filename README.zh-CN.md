# CnEquitySnapshotPipelines

[English README](README.md)

> 投资有风险。本项目不构成投资建议，仅用于学习、研究和工程审阅。

## 这个仓库是什么

`CnEquitySnapshotPipelines` 为 QuantStrategyLab 的 A 股 snapshot-backed 策略 runtime 构建 feature snapshot artifact、manifest、ranking 预览和 release summary。

本仓库只产出证据与 artifact，不下单，不保存券商凭据，也不能单独决定某个策略是否 live。

## 当前 active snapshot profile

| Profile | 名称 | 契约 | Builder |
| --- | --- | --- | --- |
| `cn_dividend_quality_snapshot` | CN Dividend Quality Snapshot | `cn_dividend_quality_snapshot.factor_snapshot.v1` | `cneq-build-dividend-quality-snapshot` |
| `cn_chinext_growth_momentum_quality_snapshot` | CN ChiNext Growth Momentum Quality Snapshot | `cn_chinext_growth_momentum_quality_snapshot.factor_snapshot.v1` | `cneq-build-chinext-growth-momentum-quality-snapshot` |

## 快速开始

```bash
python -m pip install -e '.[test]'
python -m pytest -q
```

本地构建 sample artifact pack：

```bash
PYTHONPATH=src python scripts/build_dividend_quality_sample.py
PYTHONPATH=src python scripts/build_chinext_growth_momentum_quality_sample.py
```

## 下游使用

`CnEquityStrategies` 与未来 `QmtPlatform` 应只消费经过校验的 runtime-enabled profile artifact。

因子 snapshot schema 见 [`docs/artifact_contract.md`](docs/artifact_contract.md)。

## 许可证

详见 [LICENSE](LICENSE)。
