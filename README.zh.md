# Seshat：在 XBRL 申报中找出被标记的那组计算

[English](README.md) | 中文

本仓库是 **FinProbeBench** 队伍在 [FinReason Cup 2026](https://the-finai.github.io/IEEE-bigdata-cup/) Task 3（财务审计核验）中所用系统 **Seshat** 的代码，
对应的 Working Notes 论文为
*Seshat at FinReason Cup 2026 Task 3: Finding the Flagged Computation in XBRL Filings under Hidden Rules*。

Task 3 给出一份 SEC XBRL 申报文件的节选、一个概念和一个期间，要求返回两个值：申报文件报告的值，以及由文件自身关系推出的值。
每个案例都是 XBRL US 数据质量委员会（DQC）三条规则之一（DQC_0015、DQC_0117、DQC_0126）的一条报错，但规则是隐藏的。
Seshat 从节选中恢复结构化证据，形成候选计算，再用任务本身提供的两个条件在其中做选择：
已发布规则的作用范围（上下文约束），以及“每个案例都是一条报错”这一事实（违规约束）。
它不调用任何语言模型。

| | 结果 |
|---|---|
| test（官方判分） | 97.21%，23 支队伍中第 4 名 |
| dev（官方判分） | 97.94% |
| 隐藏规则推断正确 | dev 680/680，test 680/680 |
| 运行时间 | 笔记本单核 CPU 约 2.5 秒完成 680 个案例 |

## 环境要求

Python 3.10 及以上。求解器只用标准库；运行测试需要 `pytest`。

## 数据

本仓库**不包含**比赛数据。请按官方条款从
[FinReason Cup 官方仓库](https://github.com/The-FinAI/IEEE-bigdata-cup)获取 dev 和 test 的输入文件
（`development_inputs.jsonl`、`test_inputs.jsonl`）。

## 复现最终提交

```bash
python participant/solve.py \
  --input path/to/test_inputs.jsonl \
  --output test_predictions.jsonl \
  --trace test_trace.jsonl \
  --rule-10093 --prefer-10093 --negative-policy modal \
  --observed-member-fallback --standard-member-retry \
  --no-imputation --number-format comma
```

使用官方输入时，输出与当初提交的文件逐字节一致：

| 数据集 | 预测文件的 SHA-256 |
|---|---|
| dev | `77754095288d801b85c1776c4a6fcef196a1276b5850ce56cb2ceb8e5b879004` |
| test | `16abbf035b2871d45be3ded178b97f9b220f1f4de79ab18c087a02f16cad4c8c` |

输出的每一行形如 `{"id": ..., "extracted_value": ..., "calculated_value": ...}`。
追溯文件为每个案例记录：所有候选计算、被选中的那个、被它替换的候选（如有），以及缺失子项和剩余歧义的标记。

运行测试：

```bash
python -m pytest -q participant
```

## 最终配置

| 参数 | 作用 |
|---|---|
| `--rule-10093` | 当 DQC_0117 第一条断言不触发时，评估第二条断言 |
| `--prefer-10093` | 允许单独一条第二条断言的结果取代第一条断言的结果（启发式） |
| `--negative-policy modal` | DQC_0015 有多个违规值时，返回出现最多的那个值 |
| `--observed-member-fallback` | 没有任何候选可用时，对观测到的成员做汇总 |
| `--standard-member-retry` | 未声明的汇总恰好等于总额时，只保留标准分类体系成员 |
| `--no-imputation` | 不用维度事实填补缺失的计算子项 |
| `--number-format comma` | 输出数字带千分位分隔符 |

所有参数默认关闭。`solve.py` 中的其他参数是最终配置没有使用的实验选项。

## 目录结构

```
participant/
  solve.py                 候选生成、约束、路径优先顺序、路径切换、输出
  xbrl.py                  解析器，保留被截断部分中完整闭合的元素
  dqc_rules.py             DQC_0015 的登记表与豁免、DQC_0117 的汇总轴
  dqc0117.py               DQC_0117 的两条断言（9574 和 10093）
  dqc_nonnegative.json     按分类体系年份整理的非负性登记表（见 NOTICE）
  dimension_projection.py  实验模块，最终配置未启用
  reconstruction.py        实验模块，最终配置未启用
  test_*.py                单元测试
docs/
  submission_log.md        全部 dev 提交与 test 上传记录
```

## 引用

```bibtex
@inproceedings{finprobebench2026seshat,
  title     = {Seshat at {FinReason Cup 2026} Task 3: Finding the Flagged Computation in {XBRL} Filings under Hidden Rules},
  author    = {TODO},
  booktitle = {2026 IEEE International Conference on Big Data (BigData), FinReason Cup Working Notes},
  year      = {2026}
}
```

## 许可证

代码以 MIT 许可证发布（见 `LICENSE`）。`participant/dqc_nonnegative.json` 来自 XBRL US 的 DQC 规则资源，
按其条款分发（见 `NOTICE`）。
