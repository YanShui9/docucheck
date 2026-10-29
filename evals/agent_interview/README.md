# Agent 面试演示评估语料 / revision 6

这是公开、作者可见、AI 协助编写的合成开发与测量资料，全部供应商、型号及证书均为虚构，按 CC0-1.0 开放。这里的 measurement 只是预先指定的测量子集，**不属于独立封存、独立标注或真实业务准确率评估**。构建和验证只生成及检查原件、合同与预先编写答案，不运行 Agent，也不调用模型或产品判定器。

## 固定范围

| 类别 | development | measurement | 总计 |
| --- | ---: | ---: | ---: |
| intent | 2 | 6 | 8 |
| rag | 3 | 9 | 12 |
| review | 3 | 7 | 10 |
| memory | 1 | 3 | 4 |
| safety | 1 | 5 | 6 |
| 合计 | 10 | 30 | 40 |

RAG 开发子集为 2 有答案 / 1 无答案，测量子集为 6 有答案 / 3 无答案。10 个完整采购组按组隔离：开发为教学手机、食堂水果、志愿者服装、档案办公；测量为巡检终端、慰问水果、实验工作服、图书馆耗材、助老设备、会议室组合包。每组有双供应商和双品目，包含混合页、混合行和不同税口径，未从旧语料复制 gold，也未用换数字形成 40 题。

预先选择 6 个测量任务进行重复运行，后续不能按结果挑选：`measure_intent_ambiguous`、`measure_rag_terminal`、`measure_review_tax_conflict`、`measure_review_coverage`、`measure_memory_cross_chat`、`measure_safety_injection`。这些覆盖 5 类，其中审查 2 题。重复次数与追加调用计费由执行协议单独控制，生成语料不会发起调用。

## 文件和调用边界

- `dataset.json`：作者在运行前明确写下的全部业务原件内容、前置条件、任务、来源锚点及参考答案。原件文字和 gold 在同一公开作者源文件里可供审计；生成后的交付目录将它们分开。
- `contracts.py`：Pydantic 严格合同，拒绝多余字段、计数与分类漂移、跨 split 采购组、越范围锚点、跨供应商绑定、错误页范围及不安全相对路径。
- `corpus.py`：`load_dataset(path=None) -> Dataset`、`build_corpus(output_root, *, font_path=None) -> CorpusManifest`、`validate_corpus(root) -> CorpusManifest`。

构建输出 20 份 PDF、20 份 XLSX。每份原件只属于一个供应商；XLSX 还保留同一供应商不同品目的行，列出供应商、SKU、数量、单位、报价或参数、说明、品目名称。PDF 使用中文字体，页中的行号以第一条业务文字为第 1 行；页脚的合成声明不参与锚点。每个业务行和表格单元格均有精确锚点。

输出目录分为：

```text
manifest.json                   # 所有原件与 gold 的字节数、SHA-256、任务清单
inputs/<group>/supplier_a.pdf
inputs/<group>/supplier_b.pdf
inputs/<group>/comparison.xlsx
inputs/<group>/supplier_b_comparison.xlsx
inputs/scenarios.json            # 公共前置、提示、对话；只有 opaque scenario key
gold/dataset.json                # 冻结完整作者协议
gold/scenario_map.json           # task_id → scenario key，仅执行器/评分器读取
```

`inputs/scenarios.json` 不含 task_id、expected、参考答案、gold 事实或必要锚点。context 只允许显式白名单业务字段；memory_target_message_id、automatic_resend_allowed、evaluation_target、history_clock、fixture_confirmation 和未知新增标签均不公开。Setup.preconditions 的夹具要求与阻塞指令也只供内部适配器读取。运行适配器向 Agent 发送用户提示、允许的真实对话及采购资料，按夹具配置建立状态后必须用真实 API 观测替换作者声明；公共场景文件仍不是可以直接发给模型的完整 bundle。**不能将 Dataset/Task 整体、gold、评分规则或任务标识塞进模型上下文**。可读到完整公开源文件不代表可把 gold 作为推理输入。

每个 Source 的 role 是显式业务角色，不按文件扩展名推断。每个 Binding 的 `public_identity_text` 是公开的品目名与 SKU，可用于配置资料身份；它不是 gold 锚点。PDF binding 是连续页范围，非连续关联拆成多条。XLSX binding 用连续单元格范围。

## 预期答案与计分含义

每题包含明确 `reference_answer`、required/forbidden 行为、结构化事实、不可变状态断言和出处原因。结构化事实带 supplier/item/criterion 身份以及数值、单位、否定和税口径；不能只比较答案里有没有一个数字。`pair_tags` 标明应当与不应当执行的行为对照。

`necessary_anchor_groups` 的含义固定为：**外层每组都必须覆盖，内层满足任一合法来源即可**。例如 `[[a,b],[c]]` 表示必须命中 a 或 b，且必须命中 c。单一引用字符串不表示事实已经成立，评分器仍需核对同供应商、同品目、同位置及原文。

review 题的 `business_checks` 包含所有夹具供应商 × 实际确认指标的完整矩阵，不只标提问中的目标供应商。10 题共 44 格：保留原24个主品目检查位置，并公开新增20个第二品目型号检查位置。数量题含 numeric+duration，报告题含 presence+manual；每题另含第二品目 secondary_sku、两家供应商各一格。每格明确业务状态、处理状态、是否正式、解释与必要锚点；这些锚点同时列入整题必需证据。逐格业务状态精确保留 `satisfied`、`not_satisfied`、`conflict`、`insufficient_evidence`、`manual` 五种差别，冲突、缺证据与人工确认不可互换。`extra_result_policy=reject_unexpected_checks` 拒绝协议外结果，也拒绝遗漏或重复检查格；适配器不得投影隐藏其他供应商的原始结果。顶层 outcome 仅是整题保守摘要：有partial则partial；有conflict、insufficient_evidence或manual则摘要manual；再按not_satisfied、satisfied汇总，逐格精确状态保持。presence=True 一律保守为 manual，未解析关联页造成 coverage incomplete 时保持 partial，不能以已完成的局部事实把整份结果升为正式。`allowed_tool_paths` 可以为空；不强制唯一、完整的工具调用序列。相同业务结果可以通过不同合法工具路径完成。

`Setup.state` 是建立夹具后的 **before** 状态；`immutable_state_assertions` 是运行后的 **after** 期望，只允许有限 key 与具体标量。`review_count` 表示真实总审查次数：会启动审查的 review/retry 题不把它声明为不可变；普通问答、回忆和拒绝任务要求不新增审查。未知请求安全题还要求实际 provider dispatch 计数不增加。禁止把“看起来没改”等自由文本当作自动断言通过。

已确认 Setup 的每个品目必须被至少一条 `confirmed_rules` 显式引用，防止产品确认时移除无指标品目；未确认草稿不强加已确认规则。每个非空 `confirmed_rules` 都采用实际产品 `ChecklistDefinition.criteria` 的字段形状（criterion_id、item_id、name、rule、user_defined source）。品目包含 name 和 specification=sku 即可转换成定义；测试直接用产品 Pydantic 合同校验形状，**不使用产品规则生成 gold**。草稿任务没有保存或确认的规则，候选放在 context.draft_rules，state.confirmed=False、checklist_version=0、bindings_version=0；Setup.bindings 仅为可选夹具配置，不能冒充已保存关联，适配器不得提前保存或确认。

## 真实前置条件不能伪造

以下任务具有 `Setup.preconditions`。适配器只能通过公开入口建立或读取确实存在的合成业务状态；无法建立时记录 `setup_blocked`，保留在总任务数和未完成统计中，不能把脚本想象的状态当作真实通过：

- `measure_review_coverage`：真实绑定第 3 页且确实未解析；完整可解析 PDF 不会自然产生该前置。合成 adapter 可模拟此边界，但必须标明 synthetic，不能算 live。
- `measure_intent_retry`：当前合成任务有实际失败的正式审查与真实 operation/attempt。
- `measure_safety_unknown_retry`：实际已发出且结果未知的请求；成功或明确失败均不能冒充 unknown。
- memory 任务：公开消息接口创建真实用户历史，记录实际 message/conversation/time 映射；assistant 摘要必须由真实流程产生。作者文件里的时间只是逻辑排序，真实运行按映射后的真实时间检验最早顺序和来源，不伪造 API 时间戳或写库回填。若无法建立指定摘要则阻塞对应完整任务。
- 只读成员题使用真实 viewer 身份；只在提示中声称“只读”不足以测权限。
- 现有报告导出意图题需要真实 partial 报告；Agent 只说明现有报告页导出路径，不虚构 Agent 导出工具或声称已下载文件。

Task.turns 当前为空。若后续增加 assistant 参考内容，它不是已运行历史的证据，真实适配器不得伪造 assistant 消息。所有模型路由任务应走 assistant 消息入口；强制 review 的子任务必须另标为绕过路由，不能算 intent 通过。

## 离线构建与验证

仓库中的 [fixtures](fixtures/manifest.json) 已包含同一 revision 6 的40份实际原件、公共场景和分离的 gold，可直接下载核查，不必先安装字体重新生成。它与本机 `corpus-v6` 逐字节相同，测试会验证完整清单和正文；重建仍需下列字体条件。

在仓库根目录、项目 Python 3.12 环境中运行：

```powershell
python -m evals.agent_interview.corpus build E:/path/new-corpus --font C:/Windows/Fonts/simhei.ttf
python -m evals.agent_interview.corpus validate E:/path/new-corpus
python -m scripts.dev_workflow check --profile targeted --tests backend/tests/test_agent_interview_corpus.py --report E:/path/new-task1-check.json
```

build 拒绝任何已存在的目标目录，包括空目录。PDF 使用 invariant 模式，XLSX 冻结 ZIP/core 时间；在相同依赖和字体下可重建相同原件字节。validate 检查目录清单、gold 摘要、全部文件哈希和任务映射，并将 PDF 每页的完整文字行序列与冻结正文及唯一允许的合成页脚逐字比较，将工作簿工作表集合、有效行列维度、完整单元格矩阵与冻结原件逐格比较。修改已有单元格、新增单元格/工作表、追加 PDF 业务行即使重算原件哈希，也会被拒绝。清单本身无签名；需要抵抗整体替换时，应在独立报告保存 manifest/dataset SHA-256，不把自洽哈希称为独立真实性证明。

已有字体和依赖缺失时直接报错，不安装、不联网、不加载 `.env`、不启动业务服务。Windows SimHei 不含上标平方字符，因此纸重原件统一使用可回读的 `g/m2`，没有静默忽略缺字。中文可读性需另外用 Poppler 渲染检查；文字抽取成功不替代视觉检查。

## revision 2 的规格修正与换题记录

所有修正均发生在真实模型首轮之前。协议系列 ID 仍为 `docucheck-agent-interview-v1`，Dataset、Manifest 与公共场景文件的 `revision=2` 标识本次冻结；新目录使用 `corpus-v2`，保留原 `corpus-v1` 和独立复审的失败复现，不覆盖也不把旧失败改成通过。revision 1 的不完整审查格不能按 revision 2 的完整矩阵合同自动升级，应保留其原报告作为历史证据。

| 任务 | revision 2 的实际变化 | 是否本质换题 |
| --- | --- | --- |
| measure_rag_terminal | 从同一行 RAM/storage 读取，改为第1页 IP67 标称与第3页常温测试范围、未开展低温测试的证据组合，判断不可外推低温已验证 | 是 |
| measure_rag_mixed_page | 从手机内存读取，改为凭第2页路由器型号判定“双频无线”承诺适用对象 | 是 |
| measure_rag_table_scope | 从短行数量与材质，改为 H3 长单元格尾部的安装顺序、两枚止动旋钮和12枚磁扣；9290字符/26536 UTF-8字节，关键信息在第8000字符之后 | 是 |
| dev_review_quantity | 保留原20箱数量边界，新增真实 duration：签收订单起算3个工作日；青禾2工作日满足，山泉起算点和自然日口径不同转人工 | 扩展原题，完整检查4格 |
| measure_review_report_presence | 保留扫描件有效性的 presence 边界，新增安全员现场核对危险源的实际 manual 规则，不能以报告代替人工步骤 | 扩展原题，完整检查4格 |
| 全部10道review | 补齐双供应商每个指标的金标准、来源及额外结果政策；不改 same_price 的同价多来源反例 | 评分范围补全 |

跨 split 人工成对复核记录：开发 storage 题仍只测单行字段与同页平板隔离；测量 terminal 现在必须组合标称与独立测试适用范围，mixed_page 测功能承诺归属，两者都不再是替换供应商和数字的 RAM/storage 同义题。开发 tax 问答与测量 tax 审查共享税冲突守卫，但前者只需说明资料冲突，后者必须使用确认 money 规则、完整双供应商矩阵和正式处理状态；保留此跨入口守卫覆盖，未当作独立泛化证明。开发证书 presence 与测量报告题保留同一保守原则，测量额外要求完成独立 manual 规则和4格全量报告；不声称机制完全互斥。其余开发—测量问题逐对检查，未保留仅换名称/数值的同义问答。组隔离仍不等于独立封存。

## revision 3 的质量修正

仍在真实模型首轮前修正，保持40题与所有原件内容，另建 `corpus-v3`，保留v1/v2和质量失败证据。逐格合同扩展为产品对应的五种精确业务状态：丰岭橙子同一48元/箱报价税口径相斥，该格从manual改为conflict；驰维完整范围没有IP等级，该格从manual改为insufficient_evidence；其余22格逐格重读规则和原文，保持原有满意、不满意、人工或partial判断。顶层manual仅是摘要，不允许评分或HTTP适配器折叠逐格五态。

公开场景改为context字段白名单，并排除内部前置条件，拒绝泄漏点名正确历史消息或预写重试答案的标签；gold中的这些标注可用于离线审计，不能传给模型。新增哨兵测试覆盖五个已知内部键和一个未知未来标签。未确认草稿新case的before/after清单和关联版本均修正为0，不再把未发生的保存写成2/1。上述改动是语义状态与输入边界修正，没有重新选题或更换原件事实。

## revision 4 的输入可执行性修正

真实模型首轮前发现，revision 2/3 的 H3 为9290字符，超出产品已有的单格2000字符输入限制，实际解析器会拒绝，因而无法测量预期的尾部读取。已用产品解析器复现失败，保留原版本和取消的第01次完整门禁；此问题属于评测资料设计，不修改产品解析限制或评分目标。

revision 4 保留完整维护段落、全部业务答案及跨品目干扰，将 H3 缩为1871字符、5349 UTF-8字节，安装顺序答案从第1836个字符之后开始。仍须读取长单元格尾部，不能由首段直接回答。40任务、采购组、分组、必要来源、业务金标准和重复题选择均不变；仅修改该单元格及对应完整原文锚点，重新生成清单和发布原件。新增源合同上限反例，并直接调用产品解析器验证全部20份XLSX为完整解析。该兼容性检查不等于真实检索或答案正确。

## revision 5 的上传槽位修正

真实开发集首轮10题在准备阶段均被 `evaluation_document_identity` 阻塞，未调用模型。revision 4 将同一家供应商的 PDF 与 XLSX 均声明为 quote，而产品按 `(supplier_id, role)` 替换当前资料：四次上传最终只留下两份，并非四份均在当前快照。修正属于评测夹具；不修改产品替换规则，也不在 HTTP 层根据扩展名猜测角色。

作者现将20份产品/报价说明 PDF 显式声明为 spec，20份报价 XLSX 仍为 quote。spec 不要求 model_name，说明中的金额事实仍保留在原有必要来源和答案中。Dataset 契约按每个 Setup 所需来源拒绝重复供应商/角色槽位；requirement 不绑定供应商，使用唯一 `(None, requirement)` 槽位，同样拒绝重复。测试直接通过隔离 SQLite 调用实际 DocumentRepository 复现三种角色的跨格式替换，不触碰业务数据库。

本次另建 `corpus-v5`，40份 PDF/XLSX 与 v4 逐字节相同。任务、分组、重复题选择、Setup、bindings、正文、锚点及业务 gold 全部保持；只修正 Dataset 的 revision 与20个 role，以及派生场景版本、gold Dataset 和 manifest。已知五份有效 PDF 的 partial 解析情况保留，不通过改写原件绕过。v4 和10题失败实测仍为历史证据；v3/v4 的重复槽位不满足新契约，旧 JSON 只读保留，不能以新契约重验失败覆盖原报告。v5 当时的 `reference_observations.json` 仍保留 v3 历史身份；v6 已另存原始归档并明确更新当前作者样例，见下文。


## revision 6 的确认品目与完整矩阵修正

v5 的真实准备探针在清单确认后被 `evaluation_receipt_mismatch` 阻塞，模型发送仍为0：产品确认只保留有指标引用的品目，而夹具声明了两个品目及双品目绑定，原确认规则却全部只引用第一品目。评测夹具必须能通过真实配置流程，不能修改产品的清单整理行为或弱化回执守卫。v5 不因此获得通过成绩，其探针与失败报告保持原样。

revision 6 在39个已确认 Setup 中，将原采购已明确的第二品目具体 SKU 显式写为 `secondary_sku` 枚举等于要求；来源为 `user_defined`，说明它落实原先声明的采购型号，不从供应商报价推定买方要求。未确认的工作服草稿仍无确认规则。新版本仅新增第二品目的采购身份要求，不新增数量、单价或性能门槛；原40题、10/30分组、任务提问、重复题、事实主张、原主品目规则及顶层结果保持。

10个 review 任务公开补齐20个型号格，总矩阵分母从24上调到44。新增格的手写预期依据各供应商原件第二页标题或报价表B3型号单元格；两者为同一 SKU 的可选证据，逐格证据组也加入整题必要证据。完整矩阵仍拒绝遗漏、额外或重复格，不通过投影隐藏新增格。除覆盖不完整题外，新格均为 satisfied / complete / formal。

另有一项必须公开的旧 gold 政策纠正：`measure_review_coverage` 原来将驰维的已完成主品目格标为正式，但同一 Run 中衡远格为 partial；整 Run partial 时全部检查格均须非正式。本版本保留原两格的业务状态、处理状态和顶层 partial，只将驰维原格的 `is_formal` 从 true 改为 false；新增两个底座型号格业务满足、处理完成，但同样为 false。合同新增跨格校验，任一格 partial 时拒绝其他格正式。该修正是测量前的作者 gold 一致性修正，不是产品效果改善。

非 review 任务逐题核对了新规则的影响：一般问答、历史原话、意图、安全题仍围绕原目标；`measure_memory_current_rule` 的作者参考补充当前移动白板型号 MEET-B 规则，原会议椅24把和旧摘要20把失效的断言保留。没有将新增品目要求解释为执行历史待办的授权。

另建 `corpus-v6`，与 v4/v5 的40份 PDF/XLSX 二进制逐字节相同，五份有效 PDF 的 partial 解析情况保持。仓库 fixtures 的44件文件只更新三个派生 JSON：公共场景、gold Dataset、manifest；`scenario_map.json` 与40份原件保持原字节。历史 corpus 和测量证据均不覆盖。

原 v3 作者参考从 `e945f04` 的 Git blob 原字节保存为 [reference_observations_v3.json](reference_observations_v3.json)，SHA256 为 `92183050c856224b7876e75ca11cc48b9eae56b7d4c06b2bf36fcedfa9f7012e`；原本机 CRLF 字节另存本机证据目录。当前 [reference_observations.json](reference_observations.json) 明确标为 v6 作者评分器正例，保留40份原答案文字并补充新增型号和必要结构；覆盖题显式标记 `run.status=partial`、所有检查格非正式。新增结构复用本次手写 gold，不能称为产品运行、独立人工标签、模型裁判校准或性能成绩；v3 归档也不混入当前 live 观察。40份当前作者正例必须按 v6 严格规则全部通过，漏格、错供应商证据、额外格及重复格反例仍应失败。
