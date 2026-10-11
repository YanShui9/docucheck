# 动态多品类评估三大步计划

> **For agentic workers:** Use `executing-plans` in this session. 用户要求按少量大步骤执行；每步复核并本地 Git 提交，未经明确要求不推送。

**Goal:** 为当前动态采购 Agent 建立可复现的开发评估，再单列真正独立封存质量。

**Architecture:** 资料、确认的动态清单/品目对应及参考答案先固定；执行侧只取业务输入，评分侧按供应商＋品目＋指标主键评分。旧固定八字段评估保持历史身份；新路径在 `evals/dynamic/`，不修改生产规则来适配答案。

**Tech Stack:** 现有 Python 3.12/Pydantic、PDF/XLSX 生成库、pytest、FastAPI HTTP；真实阶段使用既有 OIDC/MySQL/worker/DeepSeek/BGE-M3/Milvus。

设计：[D1 评估协议](../specs/2026-10-11-dynamic-evaluation-design.md)。D1 完成本轮资料/协议工作；D2/D3 的代码实现前读取已确认协议，细化对应契约与失败测试，不在本文件提前复制未实现的整套程序。

## D1 — 来源核实、12包蓝图与评估口径

**文件：**

- 新建 `evals/dynamic/source_catalog.json`：核实来源及未取得材料的边界。
- 新建 `evals/dynamic/case_matrix.json`：12包、55指标定义、104供应商判定位置；全部标记作者已见开发蓝图。
- 新建本计划和设计文档，更新 `docs/evaluation.md`、`docs/development-roadmap.md`、`docs/README.md`、`CLAUDE.md`、`evals/README.md`。

**完成条件：**

- [x] 核对现有评分器：固定 `FIELD_NAMES/FieldKey` 不能直接评分动态品目。
- [x] 实查公开需求和GitHub方法参考，区分正文已核实、附件未取得、不能打开的线索；不把空报价模板当已填供应商响应。
- [x] 编写12包蓝图及六规则、五状态覆盖目标；明确原件和逐格gold尚未制作。
- [x] 固定主键、分母、必要证据组、首次失败/重试、非正式/人工与版本契约。
- [x] 校验JSON、数量/ID/来源引用、全部清单Pydantic契约和入口文档；审查边界后本地提交。

**验证命令：**

```powershell
python.exe -m scripts.dev_workflow check --profile docs --report data/verification/dynamic-evaluation-d1-docs-final-2026-10-11.json
git diff --check
```

蓝图另做只读结构校验，报告写 `data/verification/dynamic-evaluation-d1-blueprints-2026-10-11.json`。预期12包/55定义/104格，六规则均出现、无重复主键、引用已核实或自编来源、独立封存0；模型调用0。D1不声称测试业务准确率。

## D2 — 合成原件、逐格答案及动态离线评分

**文件和职责：**

- `evals/dynamic/contracts.py`：包manifest、来源、必要证据组及gold的严格边界；不导入生产判定规则。
- `evals/dynamic/corpus.py`：生成合成PDF/XLSX、哈希与定位，再做原件/清单/对应/答案交叉校验；默认不联网。
- `evals/dynamic/metrics.py`：评分已取得的原始结果、可见回答与导出；只比较gold，不生成gold。
- `backend/tests/test_dynamic_eval_contracts.py`、`test_dynamic_eval_corpus.py`、`test_dynamic_eval_metrics.py`：开发工具回归。
- `data/dynamic-dev-v1/`（忽略目录）：每包 `inputs/`、`config/`、`gold/`、`review/`；当前仓库仅提交可生成的合成蓝图、复核记录和脱敏摘要。

**执行与门禁：**

1. 先按蓝图写清楚原始需求和供应商响应、采购范围；补齐全部104格的人工编写理由和证据组。独立于产品判定器复核，记录AI作者/复核属性。含报告的样本明示虚构，仅核验材料存在，不生成真实证书/签章。
2. 契约失败测试至少覆盖重复主键、漏格/多格、未知品目/供应商、来源哈希改变、定位越界、数量单位/金额口径、处理不完整却被标证据不足、gold出现在业务输入、已存在输出目录及报告覆盖。
3. 评分失败测试：少一格仍以104作分母；只引用一处不能得冲突全证据分；买方要求不能替供应商证据；接口与回答冲突计危险误判；重复/额外格导致严格包失败；partial和失败不能按正确缺失算分；重试不能覆盖首次成绩。
4. 先让上述反例失败，再最小实现并重跑。真实模型尚未参与；脚本化结果只证明评分行为。
5. 默认预览/验证不读取.env、OIDC token、业务库或网络。包导出白名单只包含inputs和确认的业务配置；gold隔离不能仅靠提示词。
6. 原件哈希、逐格答案、协议、代码/依赖身份固定后运行离线全量入口、Ruff、依赖检查及定向工具测试。失败记录保留，成功后本地提交。

**验证：** 定向运行新增三个测试文件；完整运行 `python.exe -m scripts.verify_offline --report data/verification/dynamic-evaluation-d2-offline.json`；新报告路径必须未存在。生成后逐包读回PDF/XLSX原文并核对证据定位；不以文件成功写入代替内容核验。

**交付：** 可复现开发原件、104格gold及复核摘要、评分器正反例、离线结果；独立封存仍0。若原件制作发现蓝图口径错误，在首次产品运行前记录修改、冻结新蓝图摘要；不得运行后无痕改答案。

## D3 — 明示实连、首次质量测量及独立集入口

**文件和职责：**

- `evals/dynamic/run.py`：默认预览；显式发送时新建合成工作区，通过真实HTTP上传/解析/确认/对应/审查/导出，保存每次attempt和资源ID。
- `backend/tests/test_dynamic_eval_run.py`：离线传输契约，不冒充真实E2E。
- `docs/dynamic-evaluation-d3-measurement.md`：真实执行日期/环境、固定分母、原始失败与复测分别报告。

**执行与门禁：**

1. 开始前说明需要的Docker服务、模型发送范围和调用预算；核对API/worker实际源码、迁移、OIDC及解析/Embedding配置。不在普通离线命令里启动服务或读取凭据。
2. 固定12包首次运行；运行前列出包/文件/判例数及gold摘要。严格业务主轨采用事先保存确认的清单，候选识别另测；失败、超时和unknown都保留。
3. 得到结果后用D2评分器评分，核对SQL状态、可见回答、原件及JSON/XLSX来源冻结。报告准确率、必要证据、危险满足、完成率、五状态混淆、各品类/规则、真实/未知用量。全局失败不能删除分母。
4. 明确重试独立记录；provider可能收到的请求不自动重发。按已冻结答案定位产品缺陷，修复后另开评估轮，不能覆盖首轮。
5. 独立材料另请未参与当前答案的作者/复核者提供或核查；封存前保存身份声明及哈希，不自动填写人名。未得到真正独立包时如实报告0，开发质量测量可以继续，但独立门槛未完成。
6. 另建中文检索问题和证据集合，比较BM25/向量/hybrid。top-k不参与正式缺失判断；这项对照和后续生产容量/恢复分别交付。

**交付：** 指定配置/版本下的动态开发质量及失败记录，或者实际阻塞原因；至少6包独立封存仍是未完成门槛，不能以继续开发授权代替复核。真实费用须用户账单，未知不填0。源码门禁通过后本地提交，GitHub上传仍等用户明确要求。

## 复核与回滚

- D1只增加协议资产/修正当前入口，不改现有gold、旧报告、生产代码、数据库或原件。
- 每步差异和验证与对应提交绑定；后续回滚先检查工作区，再按最近步骤`git revert`，不在主工作区`reset --hard`。
- D1前基线为`eb67ded`，原有备份和C记录保留。证据主张以实际测量的代码/协议/资料版本为准。
