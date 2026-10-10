# GitHub 更新说明

公开仓库：https://github.com/yang2267251737-cmd/power-credit-research 。默认分支为 `main`。

仓库由用户创建，首版通过已连接的 GitHub 插件上传。上传采用 Git blob/tree/commit API，完成后检查文件与提交。无需向项目提供或保存令牌。

后续可继续通过插件更新；或在本机完成 GitHub 身份验证后使用常规 Git 流程：

```bash
git clone https://github.com/yang2267251737-cmd/power-credit-research.git
cd power-credit-research
python -m unittest discover -s tests -v
python -m power_credit.build
```

更新前先检查远端历史与本地改动，创建 `codex/` 前缀的工作分支并按需要提交。不要再次运行仓库创建命令。

发布内容包括研究代码、事实 CSV、方法文档、计算结果和离线看板；`data/raw/`、提取全文、临时图像、本地数据库和环境文件被 `.gitignore` 排除。

数据库由 `python -m power_credit.build` 在本地生成，不随仓库提交。下载原文后可使用 `scripts/audit_pdf_evidence.py` 进行文件哈希、页数与数值/页码检查。
