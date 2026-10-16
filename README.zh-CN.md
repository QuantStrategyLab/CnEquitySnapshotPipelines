# CnEquitySnapshotPipelines

为 QuantStrategyLab 策略运行时构建经过验证的 A 股因子 snapshot 与证据产物的特征管线。

[English](README.md) | 简体中文

## 状态

实验性项目，使用风险自负。本项目不构成投资建议，也不保证任何交易结果。它的输出是研究证据，不是交易指令。

## 简介

CnEquitySnapshotPipelines 为基于 snapshot 的 A 股策略运行时构建因子 snapshot 产物、manifest、ranking 预览和发布摘要。它读取因子输入 CSV，或用 AkShare 从公开数据暂存输入，并在稳定契约下输出带版本号的产物包。下游策略仓与执行仓只消费经过验证的产物。

## 不适用范围

它不提交券商订单。它不保存券商凭据。它不会自行让某个策略进入实盘。

## 快速开始

需要 Python 3.11 或更高版本。

```bash
python -m pip install -e '.[test]'
python -m pytest -q
```

用内置示例输入构建样例产物包：

```bash
python scripts/build_dividend_quality_sample.py
python scripts/build_chinext_growth_momentum_quality_sample.py
```

或运行已安装的 builder：

```bash
cneq-build-dividend-quality-snapshot \
  --factor-snapshot examples/dividend_quality/factor_snapshot.sample.csv \
  --output-dir data/output/dividend_quality
```

如需从公开数据暂存因子输入，安装可选依赖：

```bash
python -m pip install -e '.[public-data]'
cneq-stage-akshare-dividend-quality --output data/staging/dividend_quality/factor_snapshot.latest.csv
```

## 配置

各 builder 通过命令行参数配置，不读取环境变量。

| 参数 | 必填 | 默认值 | 用途 |
|---|---|---|---|
| `--factor-snapshot` | 是 | 无 | 因子输入 CSV；期望列见 [`docs/artifact_contract.md`](docs/artifact_contract.md)。 |
| `--output-dir` | 否 | `data/output/<profile>` | 生成的产物包目录。 |
| `--min-adv20-cny` | 否 | `0.0` | 20 日平均成交额下限过滤。 |
| `--min-market-cap-cny` | 否 | `0.0` | 市值下限过滤。 |
| `--as-of` | 否 | UTC 当天 | snapshot 日期，格式 `YYYY-MM-DD`。 |
| `--output` | 否 | `data/staging/<profile>/factor_snapshot.latest.csv` | AkShare 暂存命令的输出路径。 |

## 仓库结构

```
src/        snapshot builder、契约与生命周期辅助代码
scripts/    样例构建脚本
examples/   示例因子输入
docs/       产物契约与设计说明
tests/      单元与契约测试
data/       本地工作输出
```

## 参与贡献

见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 安全

见 [SECURITY.md](SECURITY.md)。安全漏洞不要开公开 issue，也不要让凭据、私有数据和外部服务 token 进入 Git 或日志。

## 许可证

MIT，见 [LICENSE](LICENSE)。
