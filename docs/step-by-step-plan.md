# DocuCheck 分步协作计划

> 2026-09-27 更新：用户明确授权本次连续完成 M2 的离线代码工作，因此下文“每次只做一个 M2 小步骤”的历史约定对本次 M2 不适用。当前结果和真实 DeepSeek 待验收命令见 [M2 离线验收记录](m2-offline-acceptance-2026-09-27.md)。API、安装和 Docker 等额外操作仍由用户执行；前端代码开始前仍须先讨论设计规范和样式。

更新：2026-09-25。依据用户最新要求：先看计划，一次完成一个小步骤，不连续实施整个阶段。本文的协作节奏优先于旧实施计划中的自动执行安排。

## 一、当前现场

- 已有架构文档和 M1 任务计划。
- M1 的证据、模型适配、检索、内存规则、Agent 循环与 CLI 已按七步复核；交付范围与未实现部分见 [M1 验收小结](m1-acceptance-2026-09-25.md)。
- 已在 `docucheck` Conda 环境安装 LangGraph、LangChain 接口等依赖；依赖记录在 environment.yml 和 Windows 锁定文件。
- 步骤 2 的证据/解析测试由用户运行，13/13 通过（1.65 秒）；步骤 7 在当前代码上复测后端 90/90 通过、全目录 Ruff 通过、`pip check` 无冲突。旧的局部测试结果只作为历史过程记录。
- 用户已选择 `deepseek-flash` 和 DeepSeek OpenAI 兼容地址，并反馈 `test_provider_contract.py` 的 37 项测试通过（5.07 秒）、`test_provider_probe.py` 的 2 项测试通过（0.42 秒）；自行执行一次真实探针，返回 `MemoryProbe`、`memory_gb: 16`、输入 304 / 输出 35 token。此结果只验证单次指定工具调用，不代表完整 Agent 或业务效果。
- 步骤 4 原定检索测试由用户运行，2/2 通过（1.59 秒）。审查发现长证据的后段虽可被检索命中，却无法经 `read_source` 读取；加入按字符位置续读及回归测试后，AI 在已有 `docucheck` Conda 环境运行前四步合并离线测试，55/55 通过（4.34 秒），其中检索测试为 3 项。
- 步骤 5 的内存规则与证据校验已完成本地复核。审查修正了模型漏抽容量来源却把全量扫描记为完成的问题；前五步合并离线测试 69/69 通过（6.30 秒）。用户随后运行真实模型三批模拟资料抽取，反馈完整处理、16/16/32 GB 三处来源和冲突结论。
- 步骤 6 前两次真实运行分别暴露决策预算和工具选择问题。用户第三次运行在同一会话、同一资料快照内完成两问：首问 6 次模型调用，返回“资料冲突”及三处来源；追问 3 次模型调用，复用核验结果并读原文，返回报价和规格书两处冲突来源。追问答复还附带需求来源，属待改进的答复聚焦度。步骤 7 已整理结果、限制、依赖和保存范围，M1 在模拟包与内存字段范围内完成验收。
- 用户新增前端暂停点：开始设计或编写前端页面、组件、样式、交互前，先提醒用户并暂停实现；先讨论设计规范、页面流程和视觉样式，获得明确的前端开发指示后再改代码。

M1 的模拟包验收不代表八字段、正式上传或独立质量目标已完成；下一阶段仍由用户逐步决定，不自动实施。

## 二、每一步的协作方式

1. AI 先说明这一步解决什么、涉及哪些文件、完成后能看到什么。
2. 用户确认这一小步后，再进行对应代码工作；不顺带进入下一步。
3. 如果需要 API 配置/调用、安装依赖、启动容器或其他额外环境操作，AI 先提供目的、具体操作、预期结果和必要费用信息，由用户执行。
4. 用户反馈执行结果；不要发送密钥，可只反馈成功提示或已脱敏错误。
5. AI 解释结果与限制，并更新该步骤状态；用户确认继续后才进入下一步。

本地代码检查与测试也在该小步的说明中列明。未经用户明确选择，不启动并行开发或一次完成多个步骤。

## 三、先完成 M1 的七个小步骤

| 步骤 | 本步内容 | 可检查结果 | 用户操作 |
| --- | --- | --- | --- |
| 1. 确认业务与架构 | 阅读上传、提问、Agent 选工具、证据核验、追问的业务流程 | 明确 Agent 负责什么、工具负责什么、第一版支持什么 | 看计划并提出修改；此时不需要 API |
| 2. 复核证据基础 | 只看现有证据 ID、文件版本、原文位置、解析覆盖代码 | 更换文件后旧证据不能误用；未处理完整会明确标记 | 核对模拟原件与来源位置 |
| 3. 配置模型接口 | 解释 Provider，选择一个服务和模型，检查适配代码 | 一个最小请求能返回合法工具调用；明确请求发送内容 | 按说明在本机配置并执行一次探针 |
| 4. 复核检索工具 | 只检查找资料、读原文两个能力 | “内存/RAM”能找到需求、报价、规格书三处来源 | 核对来源是否正确 |
| 5. 复核内存业务 | 只检查内存事实抽取、引用验证与规则 | 16GB/32GB 冲突有双方证据；缺覆盖不判满足 | 如需模型抽取，按说明执行并反馈 |
| 6. 复核 Agent 循环 | 将已检查的工具接入模型决策；检查上下文和停止条件 | 可以提问、选工具、继续查证、回答、来源追问 | 按说明运行两轮演示 |
| 7. M1 小结 | 检查运行记录、限制和文档，确认如何保存代码 | 清楚哪些是实测、哪些尚未实现，再决定 M2 | 复核结果并决定是否继续 |

步骤 1～5 完成各自小范围验收；步骤 6 第三次真实运行的两问均完成，记录证实追问复用同快照核验结果并读取原文；步骤 7 已完成 M1 小结及最终本地检查。M1 完成条件只适用于本模拟包和内存字段，不连续进入 M2。

### 步骤 1：业务流程与职责说明（供用户确认）

**用户操作。** 创建采购任务，上传一份需求文件及每家供应商的报价、规格书，并在上传时标明文件角色和供应商。系统先校验文件、解析可支持内容、建立带页码或单元格位置的证据。用户可以提出具体问题，也可以要求完整审查；若资料还在处理，问题等待所需资料就绪。

**Agent 操作。** 用户提问后，模型理解问题和对话中的指代，再决定调用哪些白名单工具。它可以先找相关证据、查看原文，再要求抽取事实、调用核验；证据不够时可以改写查询或请求澄清。每次工具调用都返回可检查的结果和来源，Agent 据此决定下一步。Agent 只能使用当前任务及文件版本中的证据。

**服务端操作。** 解析器提供原文位置，检索工具找材料，事实校验确认模型提出的数值确实出现在相应原文中，Python 规则计算正式业务状态，答复校验确认引用有效。模型可以选择行动和组织提问，不能替用户指定文件归属、修改事实或决定最终采购。

**具体例子。** 需求写“已装内存至少 16 GB”；供应商 A 报价的“报价!B6”写“已装内存 16 GB”，规格书第 1 页写“已装内存 32 GB”。用户问“供应商 A 的内存是否达标？”Agent 应查看需求及 A 的两份资料，发现同型号、同口径的两个值不一致，调用规则后回答“资料冲突”，同时显示这三处来源。用户追问“哪两处冲突？”时，可沿用本任务与供应商语境，重新读取或引用同一输入版本的报价与规格书原文。

**结果边界。** 五类业务状态为满足、不满足、资料冲突、证据不足、待人工确认。文件解析、抽取或模型调用失败时，运行显示未完成及受影响范围，不把它伪装成“证据不足”。正式审查要覆盖全部适用资料；检索前几条未命中，不能据此断言原件没有该字段。用户修正事实须给理由和来源，再生成新版本重算；采购决定仍由用户作出。

**第一段可运行范围。** M1 先用模拟文档包和命令行验证“内存问题 → 真实模型选工具 → 冲突结论与来源 → 追问”。八字段完整审查、网页上传、持久化会话依后续步骤实施，不能把 M1 演示描述为整套产品完成。

本步骤的确认点是：上传后由用户提问启动 Agent；Agent 能决定如何取证；正式业务状态由规则和证据决定；不完整资料不产生假结论；用户可追问来源。若这五点符合预期，再进入步骤 2 复核证据与文件版本代码。

### 步骤 2：证据与文件版本复核（本步已核对）

**本步检查的事实。** 开发样例的 `manifest.json` 明确绑定一份需求、供应商 A 的报价和规格书，并保存每个原件的 SHA-256；PDF 的开发解析缓存也有独立哈希。加载时会验证哈希。证据 ID 由 Case、输入版本、源文件哈希、解析版本和内容位置生成；读取时只接受本快照的 ID。PDF 保留零基页码和 bbox，XLSX 保留工作表和单元格。现在的加载器只为可信开发样例服务，尚未实现正式上传 API。

**本步发现并修正。** 如果 MinerU 标记整份 PDF 已处理，但某页没有任何非页码正文块，旧代码可能将该页记为 `complete`。现把这类页记入 `coverage_issues`，文档结果为 `partial`；缺失或格式错误的块结构会被拒绝。增加了对应的回归测试。此前已有测试覆盖跨 Case/版本引用、修改原件后的缓存失效，以及未知内容块不能算完整。

**原件人工核对。** 打开开发样例：需求 PDF 第 1 页应有“已装内存至少 16 GB”；报价 XLSX 的“报价!B6”应为“已装内存 16 GB”；规格书 PDF 第 1 页应为“已装内存 32 GB”。核对的是原件，不使用 `labels.json` 充当运行时证据。

**用户执行的本地验证。** 在项目根目录打开 PowerShell，运行以下命令。只用已有 `docucheck` Conda 环境和本地模拟文件，不调用模型 API，也不启动 Docker。Pytest 可能在系统临时目录写测试文件。

```powershell
E:\developTools\miniconda3\envs\docucheck\python.exe -m pytest backend/tests/test_evidence_scope.py backend/tests/test_parsing.py -q
```

用户于 2026-09-25 在 `docucheck` 环境执行上述两个测试文件，反馈 `13 passed in 1.65`。AI 另以只读方式直接读取原 PDF 第 1 页文本和原 XLSX 的“报价!B6”，确认三处分别为“已装内存至少 16 GB”“已装内存 16 GB”“已装内存 32 GB”。此核对覆盖文字与单元格，不等于 PDF bbox 的可视化定位验收。

**当前限制。** 结构上标记 `complete` 仍不能保证每个自然语言事实都抽取正确；完整业务覆盖还需要后续的逐字段扫描和原件质量评测。现在也没有正式文件上传、数据库中的版本记录或跨进程恢复。

### 步骤 3：DeepSeek 模型接口与单次探针（本步已验收）

**本步内容。** 已选 DeepSeek 的 OpenAI 兼容地址 `https://api.deepseek.com`。官方当前列出 `deepseek-flash` 与 `deepseek-v4-pro`，都支持工具调用；建议先用成本较低的 `deepseek-flash` 做一次能力探针，用户可改选 Pro。参考：[模型与计费](https://api-docs.deepseek.com/quick_start/pricing/)、[首次调用](https://api-docs.deepseek.com/guides/harness)。实际模型 ID 由用户在本机 `.env` 决定。

**兼容约束。** DeepSeek Chat Completion 默认启用思考模式，而本项目的结构化抽取会指定工具名；官方说明指定工具在思考模式下返回 HTTP 400。因此 DeepSeek 适配在当前 M1 调用中明确发送 `thinking: disabled`。官方要求输出上限字段为 `max_tokens`，适配器把 LangChain 生成的 `max_completion_tokens` 改为该字段。当前不承诺 DeepSeek 思考模式；后续若启用，需要单独设计工具调用和跨轮 `reasoning_content` 传递。参考：[Chat Completion API](https://api-docs.deepseek.com/api/create-chat-completion/)、[Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)、[DeepSeek 的 `max_tokens` 配置说明](https://api-docs.deepseek.com/quick_start/agent_integrations/oh_my_pi/)。

**发往服务的内容。** `python -m backend.app.provider_probe` 最多发出一次 Chat Completion 请求；请求只含两条短消息（要求从模拟句子抽取内存，以及“模拟句子：已装内存 16 GB”）、一个 `MemoryProbe` 工具 JSON Schema、模型 ID、`thinking: disabled` 和 128 token 输出上限。不读取或发送真实采购文件，也不运行整段 Agent。API key 仅用于请求认证；程序只打印工具名、抽取值和 token 用量，不打印密钥或完整响应。模型可能拒绝工具调用，失败将返回脱敏错误码；不自动重试。

**用户本机操作。** 在 [DeepSeek 开放平台](https://platform.deepseek.com/) 创建 API key，查看账户余额和[当前计费](https://api-docs.deepseek.com/quick_start/pricing/)。截至本次核对，`deepseek-flash` 按输入/输出 token 计费，价格随峰谷及缓存命中变化；首次小探针通常只消耗很少 token，仍可能产生费用。不要把 key 发到聊天中。保留现有 `.env` 的 MySQL 两行，只补/改以下模型配置：

```dotenv
DOCUCHECK_MODEL_PROVIDER=openai-compatible
DOCUCHECK_MODEL_ID=deepseek-flash
DOCUCHECK_MODEL_BASE_URL=https://api.deepseek.com
DOCUCHECK_MODEL_API_KEY=在本机填入你的密钥
DOCUCHECK_MODEL_CONTEXT_WINDOW=1000000
```

在已激活 `docucheck` Conda 环境的项目根目录，先执行不联网的定向测试：

```powershell
python.exe -m pytest backend/tests/test_provider_contract.py backend/tests/test_provider_probe.py -q
```

测试通过后，再由用户自行执行一次真实探针：

```powershell
python.exe -m backend.app.provider_probe
```

预期看到 `"tool_call": "MemoryProbe"` 和 `"memory_gb": 16`，以及 token 用量；成功仅证明这个模型能在当前请求格式下返回一次合法指定工具调用，不证明 Agent 业务链路或八字段审查已通过。若失败，只反馈 `probe_failed` 错误码和脱敏测试结果，不盲目重复调用；尤其不要回传 `.env`、API key 或原始含敏感信息的响应。

**用户反馈（2026-09-25）。** `python.exe -m pytest backend/tests/test_provider_contract.py` 为 `37 passed in 5.07s`。`python.exe -m backend.app.provider_probe` 返回 `{"tool_call": "MemoryProbe", "memory_gb": 16, "input_tokens": 304, "output_tokens": 35}`。随后用以下命令补跑探针脚本的离线测试，结果为 `2 passed in 0.42s`：

```powershell
python.exe -m pytest backend/tests/test_provider_probe.py -q
```

**后续 API 使用。** 步骤 4 只复核本地词法检索和原文读取，使用模拟包运行离线测试，不需要模型 API。步骤 5 中规则与证据校验可离线核对；若要验证模型抽取，会再次调用 API。步骤 6 的真实 Agent 两轮对话需要 API，且每轮可能产生多次模型调用。执行这些真实调用之前先说明确切命令、发送的模拟资料范围和可能费用，由用户自行运行；本步不自动进行。

### 步骤 4：检索与原文读取（审查修正已通过离线测试）

**本步检查。** `Retriever.search` 只使用当前 `EvidenceRepository` 快照；指定供应商时仅在该供应商的报价、规格书及公共需求中搜索。`RAM`、`memory` 会归一为“内存”，中文由 jieba 分词，再用 BM25Plus 排序。命中结果带原始 `evidence_refs`；`read_source` 先验证全部 ID 属于当前快照，再返回原文与 PDF 页码/bbox 或 XLSX 工作表/单元格。原文及相邻上下文有字节上限，截断会明确标记。现阶段是词法检索，没有向量索引；检索无命中不能证明原件不存在该字段。

**已通过的定向测试。** 对开发模拟包搜索 `RAM`、限定 `vendor-a`，明确要求找回三份来源：需求 PDF 第 1 页“至少 16 GB”、报价表 `报价!B6` 的 16 GB、规格书 PDF 第 1 页的 32 GB。另核对同表相邻单元格保留原位置、陌生证据 ID 即使与有效 ID 混用也会拒绝。用户运行结果为 `2 passed in 1.59s`。

**本次审查发现并修正。** 长证据会拆成多个检索片段，但原先 `read_source` 固定只给前 800 字节；若命中词在后段，无法再从原件读取。现在工具允许对单一证据传 `start_char`，返回 `next_char` 供下一次续读；位置按 Unicode 字符计数，输出仍受 800 字节上限约束。新增的第三项测试构造了长中文证据，在尾部放检索词，验证检索命中后可通过工具连续读回完整原文，并拒绝超范围位置。修正随前四步合并离线测试通过。

用户先前在已激活 `docucheck` Conda 环境的项目根目录运行下列原定定向测试，**没有调用 DeepSeek，也不需要 Docker**：

```powershell
python.exe -m pytest backend/tests/test_retrieval.py -q
```

用户前次运行 `2 passed in 1.59s`；审查修改后，AI 执行以下前四步合并离线测试，结果为 `55 passed in 4.34s`，其中本文件 3 项通过。测试只验证模拟包的三处来源和长原文续读，不代表所有自然语言改写的召回率已达标。

```powershell
E:\developTools\miniconda3\envs\docucheck\python.exe -m pytest backend/tests/test_evidence_scope.py backend/tests/test_parsing.py backend/tests/test_provider_contract.py backend/tests/test_provider_probe.py backend/tests/test_retrieval.py -q
```

### 步骤 5：内存抽取、引用核验与确定性规则（模拟包已验收）

**本步检查。** 模型逐批看到当前快照的全部适用证据块，提出包含原始数值、单位、已装/最大口径、比较符和单一证据 ID 的候选事实。服务端验证证据 ID 的供应商与版本归属，并在原文中核实数值、单位及口径；模型不能自行给出正式业务状态。Python 规则使用 `Decimal` 比较需求与报价/规格书，并独立给出结果和引用。

**本步发现并修正。** 旧 `complete` 只表示全部证据块已发送给模型；若模型漏掉规格书的 32 GB，仍可能仅凭报价 16 GB 判“满足”。现在针对原文中含内存/RAM 与 GB/GiB 数值的块，要求至少有一条经验证的事实覆盖该块；否则 `complete=false`，规则保持 `processing_status=partial`、`business_status=null`。这是保守的遗漏拦截，不保证识别所有自然语言表达或模型抽取错误。增加的离线用例同时覆盖三处正确抽取和模型漏抽规格书的情形。

**离线验证。** AI 在已有 `docucheck` Conda 环境执行以下命令，结果 `69 passed in 6.30s`，无 DeepSeek 调用：

```powershell
E:\developTools\miniconda3\envs\docucheck\python.exe -m pytest backend/tests/test_evidence_scope.py backend/tests/test_parsing.py backend/tests/test_provider_contract.py backend/tests/test_provider_probe.py backend/tests/test_retrieval.py backend/tests/test_memory_checks.py backend/tests/test_memory_extract_probe.py -q
```

**真实抽取探针，由用户执行。** 在已激活 `docucheck` Conda 环境的项目根目录，先运行不联网预览：

```powershell
python.exe -m backend.app.memory_extract_probe
```

当前预览返回 `model_requests: 3`、`evidence_blocks: 35`。确认后再自行运行：

```powershell
python.exe -m backend.app.memory_extract_probe --send
```

此命令只读取经哈希核验的 `examples/dev-pack-001` 模拟资料，通过已有 `.env` 中的 DeepSeek 配置，将需求 PDF、报价 XLSX、规格 PDF 解析后的 35 个文字证据块分三次请求发送到模型，连同抽取指令与 `MemoryCandidates` 工具 schema。它不发送 PDF/XLSX 二进制文件，不访问真实采购资料、不运行 Agent 循环或 Docker；最多三次请求，每次输出上限 512 token、超时 30 秒，不自动重试。API 按实际 token 使用计费，执行前可在服务商平台查看余额与当前计费。不要发送 API key 或 `.env`。

理想输出包含需求 `16 GB`（PDF 第 1 页）、报价 `16 GB`（`报价!B6`）、规格书 `32 GB`（PDF 第 1 页），且 `complete=true`、`processing_status=complete`、`business_status=资料冲突`；实际调用次数与 token 用量会一并显示。若模型漏抽、拒绝调用或校验失败，保留原始退出结果并反馈脱敏的状态/错误码和请求次数；不要盲目重复付费调用。只有实测符合上述三来源与冲突结果，才把步骤 5 的真实抽取标为验收通过。

**用户实测反馈（2026-09-25）。** 预览为 3 次请求、35 个证据块。真实抽取为 3 次请求，输入 3812 / 输出 308 token，`complete=true`、`processing_status=complete`；需求 PDF 第 1 页 16 GB、报价 `报价!B6` 16 GB、规格书 PDF 第 1 页 32 GB，符合模拟包原件。用户消息中的 `business_status` 粘贴为“资 料冲突”，中间多一个空格；当前规则与探针的代码均使用精确字符串“资料冲突”，探针仅在此字符串下返回成功。记录此转录差异，不重新发起付费请求。此步的模拟资料抽取和冲突判断按用户反馈通过；完整 Agent 对话另在步骤 6 验收。

### 前端设计前的暂停点（用户新增约定）

今后进入任何前端页面、组件、样式或交互的设计与实现前，先提醒用户并暂停前端代码操作。先讨论产品页面流程、信息层级、视觉风格、组件与样式规范、响应式和可访问性等，形成用户认可的方案；用户明确同意开始写前端代码后，再按已确认方案逐步实施。此暂停点适用于后续 M4，也适用于任何提前出现的前端工作；一般的“继续”不自动解除。

### 步骤 6：Agent 循环、上下文与停止条件（离线及真实两轮验收已通过）

**本步检查。** `LangGraph StateGraph` 在模型决策、白名单工具、工具结果和下一次模型决策之间循环；`submit_answer` 与 `request_clarification` 显式结束。服务端注入不可由模型修改的 Case/快照/会话上下文，保留最近完整问答和最近工具调用/观察配对。`Budget` 对决策、工具、总模型请求、输入/输出 token 和期限设限，内层三批事实抽取也计入同一运行预算。正式答复只能从已验证的 `CheckResult` 和当前快照证据渲染；取消、超时、预算耗尽和无效引用会停在明确状态。

**本步发现并修正。** 同一供应商先有未完成核验、后有完整核验时，来源追问原来会取最早结果；现在取最新结果。另一个问题是已获得模型实际 token 用量后，预算仍把之前的保守字节估算全部算作输入占用，可能使正常多步取证过早结束；现在用回执中的实际输入 token 替换该次预留，缺失回执时继续保留保守预留。两处修正均先有失败用例，再修代码。

**离线验证。** 原 Agent 与 CLI 定向测试 12/12 通过；第一次修正后第 6 步定向测试 14/14 通过，后端完整离线测试曾为 85/85。首次真实运行暴露上下文丢失后补回归用例，第二次真实运行暴露决策工具选择未强制后补 Provider 契约测试。当前后端完整离线测试为 `90 passed in 5.11s`，修改的 Provider/测试文件 Ruff 检查通过；模拟 HTTP 请求已核对发往 DeepSeek 适配器的 `tool_choice=required`。离线测试不接触 DeepSeek，不证明真实模型下一次一定正确选工具。

**两轮真实对话，由用户执行。** 在已激活 `docucheck` Conda 环境的项目根目录运行：

```powershell
python.exe -m backend.app.agent_demo --case-dir examples/dev-pack-001 --provider configured --interactive --output data/agent-demo
```

程序出现 `问题>` 后先输入第一问。只有第一问显示 `completed` 且给出规则的“资料冲突”及来源，才继续输入第二问；若显示 `partial`、`failed` 或 `waiting_user`，直接输入 `exit` 并反馈结果。第二问结束后输入 `exit` 或 `/exit` 结束会话：

```text
供应商 A 的内存是否达标？
你根据哪两处判断冲突？
exit
```

CLI 读取经哈希核验的模拟包，不需要 Docker。模型服务会收到当前问题、受控工具 schema、模拟文档清单、Agent 选中的证据/工具结果；若 Agent 调用 `extract_facts`，三份资料的 35 个文字证据块也会分批发送。原始 PDF/XLSX 二进制文件及 `.env` 不发送。每问最多 8 次决策、16 次模型请求、16 次工具执行、累计输入预算 48k token（请求前按字节保守预留）、输出预算 6k token、总时限 120 秒；单次模型请求最多 30 秒、输出最多 512 token，不自动重试。两问理论上最多 32 次模型请求，实际由 Agent 选择的路径决定，按实际 token 计费；运行前可查看服务商余额。

预期第一问显示 `completed`、“资料冲突”、报价 `报价!B6` 的 16 GB 和规格书第 1 页的 32 GB；第二问在同一 CLI 会话中再次返回两处来源，不重新把上轮自然语言答复当证据。每问会在被 Git 忽略的 `data/agent-demo/` 保存一个 JSON 记录，包含状态、脱敏事件和用量。若某问显示 `partial`/`failed`/`waiting_user`，保留该结果，不盲目重复付费调用；只反馈终端状态、答案和记录中的工具名/错误码、模型调用数，不要发送密钥或 `.env`。真实两轮结果核对后才将步骤 6 标为验收通过。

**首次真实运行与诊断（2026-09-25）。** 用户在同一 CLI 会话中问了三次：首问 `partial / decision_budget`，8 次决策、11 次模型调用；记录显示模型已经完成 `extract_facts → evaluate_checks`，之后又重复这两个工具、读取原文，并有一次 `submit_answer` 被服务端拒绝，最终没有正式答复。第二问通过 `get_check_results → read_source → submit_answer` 返回报价 `报价!B6` 与规格书第 1 页两处原文（`completed`，3 次模型调用）；它只证明来源追问分支可用，不改变首问失败的事实。第三次输入 `exit` 而非 `/exit` 被当成问题，产生一次额外模型调用并返回 `one_tool_required`。本地 JSON 事件没有保存被拒绝的答复参数，故不能断言唯一拒绝原因；代码复核及失败用例确认了上下文压缩会在 `read_source` 后丢失较早的 `fact_set_id` / `check_result_id` 工具消息。现已保留这些必要句柄的完整工具调用/结果配对，并识别 `exit`；第二次真实运行结果见下段。

**第二次真实运行与修正（2026-09-25）。** 用户运行同一两轮对话：首问 `completed`，7 次模型调用，返回“资料冲突”，列出需求 PDF 第 1 页 16 GB、报价 `报价!B6` 16 GB、规格书 PDF 第 1 页 32 GB；记录为 `data/agent-demo/6ab9d045e6d441ea9299131559aa163d.json`。追问 `failed / one_tool_required`，1 次模型调用、0 次工具调用，记录为 `data/agent-demo/5bca9ce8264a4f48b771b9cff728b963.json`。该决策响应是普通文字，输出 186 token，未达到单次 512 token 上限；Provider 当时未发送 `tool_choice`，默认允许普通文字，而图节点要求恰好一个工具调用。先添加离线失败测试，再将决策请求改为 `tool_choice=required`，仍由模型选择具体白名单工具；抽取保持指定工具，DeepSeek 适配器继续关闭思考模式。后端 90/90 离线测试通过。AI 未发起真实 API 请求；两轮连续成功仍待用户重测。

**第三次真实运行与验收（2026-09-25）。** 用户运行同一 CLI 会话，记录 `data/agent-demo/b69afd0d981b42688c313fc7bb8f9bea.json` 为首问 `completed`、6 次模型调用、10,342 输入/524 输出 token；工具路径 `extract_facts → evaluate_checks → submit_answer`，规则返回“资料冲突”及需求 16 GB、报价 16 GB、规格书 32 GB 三处来源。记录 `data/agent-demo/4c235603a626451d90f63b77f326e414.json` 为追问 `completed`、3 次模型调用、7,019 输入/257 输出 token；工具路径 `get_check_results → read_source → submit_answer`。两次记录的 `conversation_id` 与 `snapshot_id` 相同，追问没有重新抽取事实，回到报价 `报价!B6` 和规格书第 1 页原文。追问答复还列出了需求 PDF 来源，内容正确但没有只呈现被问到的两处冲突来源；记为答复聚焦度改进，不影响本步骤“真实模型完成核验并追问来源”的验收。此记录仅覆盖一份模拟包、一次成功两轮，不等于八字段或正式质量目标通过。

### 步骤 7：M1 小结与代码保存（已完成）

完整实测、两次失败记录、验收边界和未实现部分见 [M1 验收小结](m1-acceptance-2026-09-25.md)。本机当前代码后端测试 `90 passed in 4.03s`，`ruff check backend/app backend/tests` 通过，`pip check` 无冲突；模拟包 `RAM` 检索在 12 条上限内找回三处相关来源 3/3。`.env` 与 `data/` 已由 Git 忽略，代码保存只包含 M1 实现、测试、模拟清单、依赖记录与文档。进入 M2 前需另行明确下一小步；前端暂停点持续有效。

## 四、模型接入时给用户的操作说明格式

步骤 3 的 DeepSeek 具体操作已写在上节；模型 ID 推荐先用 `deepseek-flash`，用户可自行改选。提供针对该服务的说明：

1. 到哪里创建或获取 API key，以及该服务的计费信息。
2. 在项目 `.env` 填写哪些变量；保留已有 MySQL 配置，不覆盖整个文件。
3. 如缺依赖，给出在 `docucheck` Conda 环境中的安装命令及用途。
4. 给出一条最小探针命令，注明只发送模拟内容，以及如何判断成功。
5. 说明应反馈哪些脱敏结果；失败时先定位，不让用户反复盲重试。

步骤 1～7 在各自约定范围内已完成；真实采购文档、八字段和正式质量目标尚未验收。M2 另起下一小步，前端工作继续受设计讨论暂停点约束。

## 五、关联材料

- [整体 Agent 架构](superpowers/specs/2026-09-24-agent-first-design.md)：作为技术参考，按步骤阅读相关章节即可。
- [M1 详细任务](superpowers/plans/2026-09-24-agent-mvp.md)：保留任务拆分，执行节奏服从本文。
- [整体路线图](development-roadmap.md)：M1 之后才逐步展开八字段、持久化后端和界面。
