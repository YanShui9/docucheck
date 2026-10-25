# 多来源一致报价修复计划

> **For agentic workers:** 按已授权缺陷范围连续执行；使用 systematic-debugging、test-driven-development 和 verification-before-completion。实现后独立复审，当前已有隔离工作区，不重复创建。

**Goal:** 同一供应商、同一确认品目与 SKU 的多份资料明确给出同一单价时，不再仅因价格行超过一行转人工确认。

**Architecture:** 新增绑定规则 v15；v14 及此前事实、检查、缓存和历史身份不变。服务端继续扫描全部冻结关联范围；仅对完整、连续、有限语法的无条件单价计算金额、币种、计价单位、显式税口径签名。所有价格来源独立保留，不拼接跨文件原文，不把独立税说明合入主报价。

**Tech Stack:** Python 3.12、Decimal、Pydantic、pytest；现有 EvidenceRepository、动态规则与报告导出。

## 已确认原因与边界

d01 原回执为 completed，Excel `含税单价 / 含税单价 5800 元/台` 与 PDF `含税单价 5800 元/台` 的值、币种、单位和含税口径一致。现有 `unique_price` 要求 `len(price_rows) == 1`，因此产生 `money_price_scope_unclear`。

本次仅在两行以上且每个来源都能独立解释为同一无条件单价时解除价格歧义。不得以买方阈值/单位补足来源，金额相同不足以认定一致。未知身份、不同 SKU/币种/单位/税口径、缺税口径、不同金额、范围/条件/预算/总价、碎片行及额外文字继续保留人工确认。税扫描可识别已证明一致的完整价格行自身含税说明，但单独税说明仍只接受既有同关联 Excel 唯一价格关系；跨文件独立税说明不因同价自动继承。显式元数据排除及来源数量上限不变。

2026-10-25复审收窄：全部关联行须正向分类；仅完整价格、明确报价元数据、纯品目身份、完整独立税说明，以及服务端有限支持的数量/容量、交付期限、商品状态和材料声明行可参与同价例外。未知备注、缺数字的价格待定和任意清单标签不能被忽略；数字和币种内部空白不得拼接。完整其他指标行仅用于排除价格歧义，不代表该指标满足或材料正文已验证。原d01含合成声明、正文备注等额外文字，不为提高该包分数增设专用豁免；按原范围复审保留实际结果，另用最小完整双文件同价和冲突资料验证支持边界。旧18包总成绩保持。

## 执行与验收

- [x] 1. 保存修复前 v14 事实/检查/批次/input 身份及 d01 原始来源摘要；不改原成绩。
- [x] 2. 新建 `backend/tests/test_money_consistency.py`，覆盖跨 XLSX/PDF、重复表头、同价超阈值、Decimal 表达等价、来源完整与历史重校验，并参数化上述保守反例。
- [x] 3. 运行离线红测，确认失败为同价仍 unclear，而非导入或环境错误。
- [x] 4. 在 `money_sources.py` 增加有限一致报价解析；在 `facts.py`、`agent/dynamic.py` 接入 v15 并显式保留 v14；更新默认版本断言。
- [x] 5. 定向规则、Agent、仓储/导出、旧版本重校验测试及独立代码复审；全部源码修改结束后冻结完整离线门禁。
- [x] 6. 本地 Git 保存；固定提交构建部署，核对实际服务源码、原件和旧 WIP。指定 d01 新 Run 及必要冲突场景实测，保留原 Run、全部请求/失败/用量与新导出。
- [ ] 7. 更新交付文档和入口，文档门禁、本地提交、同步 GitHub、检查相应 CI。旧 18 包成绩保持原版本，本次指定复审另记。

红测与定向命令（PowerShell，工作区根目录）：

```powershell
& 'E:\developTools\miniconda3\envs\docucheck\python.exe' -m pytest backend/tests/test_money_consistency.py -q -p scripts.offline_guard
& 'E:\developTools\miniconda3\envs\docucheck\python.exe' -m pytest backend/tests/test_money_consistency.py backend/tests/test_money_sources.py backend/tests/test_money_quote_metadata.py backend/tests/test_money_agent.py backend/tests/test_money_repository.py backend/tests/test_money_response.py -q -p scripts.offline_guard
```

冻结后使用 `scripts.verify_offline`；证据另存本机 `data/verification/money-consistent-20261024/` 新文件，禁止覆盖失败记录。真实请求累计延续预警轮 103 的锚点，当前持久化 146，即本轮已用 43；本任务起始真实模型请求 0。

2026-10-25：第1–6项执行并留证，源码/离线/部署通过，但同价与不同价真实抽取均partial，业务验收未过；原d01仍人工，不能把步骤执行视为质量出口通过。详见[本步记录](../../consistent-money-sources-2026-10-25.md)。第7项等待文档提交、远端和CI核对。
