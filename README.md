# 电力行业发债主体偿债能力与信用风险比较

An evidence-linked credit research dataset for listed Chinese power issuers.

**状态：v0.1，可运行的历史财务研究初版。** 已录入华能国际、华电国际、大唐国际的 2025 年数据与部分 2024 年比较数，共 62 条附来源和页码的事实。尚未完成三年信用研究、债券利差分析或独立信用评级。

![Data validation](https://img.shields.io/badge/data-62_sourced_facts-087f83)
![Python](https://img.shields.io/badge/Python-3.12%2B-4676ae)

![研究看板预览](dashboard/preview.png)

## 研究问题

在持续资本开支背景下，发行人的经营现金流、受限资金与债务到期结构，如何共同影响偿债能力？会计权益列报的永续债与合并范围变化，又会怎样影响同行比较？

## 快速开始

运行核心计算只需要 Python 3.12 或以上版本，无第三方运行依赖：

```bash
python -m unittest discover -s tests -v
python -m power_credit.build
```

随后双击 `dashboard/index.html`，可以离线查看发行人比较、现金流情景与逐条证据。也可以运行：

```bash
python -m http.server 8765 --bind 127.0.0.1
```

并访问 `http://127.0.0.1:8765/dashboard/index.html`。

如需核对原始 PDF，运行 `python scripts/download_sources.py`；文本提取和原文数值检查工具另需安装 `pypdf`，再运行 `python scripts/extract_pdf_text.py` 或 `python scripts/audit_pdf_evidence.py`。原始文件与提取全文留在本地，不进入版本控制。

本次已核对三份文件的哈希与页数，62 条原始数值均能在登记的 PDF 页找到；标签、年份列和经济含义仍须结合原表核验，数值出现检查不能替代人工复核。

## 数据设计

| 文件 | 内容 |
|---|---|
| `data/issuers.csv` | 发行人全称、代码与样本说明 |
| `data/sources.csv` | 公开年报链接、披露日期与核查日期 |
| `data/source_checksums.csv` | 本次核查的原始文件哈希与 PDF 页数 |
| `data/facts.csv` | 原始数值、币种、单位、期间、合并口径、页码、重述标记与核验状态 |
| `data/processed/comparison.csv` | 衍生指标，金额统一为人民币千元 |
| `data/processed/credit.sqlite` | 本地生成的 SQLite 数据库，不提交版本控制 |
| `data/processed/research.json` | 看板使用的结构化数据 |
| `dashboard/index.html` | 不依赖外部脚本的交互看板 |

所有事实保持年报原单位；计算时使用 Decimal 统一成人民币千元。看板金额转换成亿元。缺失值不填零，缺少分母的比率不计算。

`agent_verified` 表示 AI 已依据原文核对，**不代表你已经独立复核**；你的复核完成后可改为 `user_verified`。

## 初版指标

- 资产负债率、流动比率。
- 货币资金及扣已披露受限资金后现金对一年内到期有息债务的覆盖。
- 全年经营现金流对期末有息债务的覆盖、短期债务占比。
- 经营现金流减购建长期资产支付现金的代理指标。
- 将权益列报永续债 100% 重分类为负债的杠杆敏感性情景。
- 经营现金流下降 20%、购建支出增加 10% 的假设情景。

公式、定义与限制见 [指标口径](docs/methodology.md)。上述指标不生成综合信用排名，也不将情景现金流直接解释为偿债缺口。

## 当前数据的关键口径

1. 样本是三个上市公司发行人，财务数据统一为合并口径；不使用其控股集团财务数据替代。
2. 2024 年财务比较数来自 2025 年报告。华电国际已重述，其他样本采用报告比较数。此数据集不能直接作为历史时点回测输入。
3. 2025 年有息债务和一年内到期债务采用债券章节合并口径披露值，金额精度不同；须继续核对各发行人的债务定义。
4. 华能国际另有境外资金汇回限制。其与受限货币资金是否重叠尚未确认，因此不直接相加扣减。
5. 2024 年可比债务口径尚待核对，相关比率留空。
6. 资料核查日期为 2026-10-10，财务快照为 2025 年末。2026 年半年报与后续融资事件尚未纳入。

## 下一阶段

详见 [实施路线](docs/roadmap.md) 和 [协作与复核清单](docs/collaboration.md)。计划补齐 2023–2025 年数据、母公司口径、债务分类、授信及再融资资料，再形成主体一页纸和比较报告。

## 公开来源

- [华能国际 2025 年度报告（港交所披露）](https://www1.hkexnews.hk/listedco/listconews/sehk/2026/0325/2026032500011_c.pdf)
- [华电国际 2025 年年度报告（巨潮资讯）](https://static.cninfo.com.cn/finalpage/2026-03-27/1225039299.PDF)
- [大唐国际 2025 年年度报告（港交所披露）](https://www.hkexnews.hk/listedco/listconews/sehk/2026/0327/2026032703594_c.pdf)

仓库只保留研究代码、整理的财务事实、计算结果和公开来源链接；原始年报全文不随仓库分发。

## GitHub 发布

公开仓库：[yang2267251737-cmd/power-credit-research](https://github.com/yang2267251737-cmd/power-credit-research)。CI 会运行数据验证和财务计算测试。更新方式见 [GitHub 发布说明](docs/github.md)。
