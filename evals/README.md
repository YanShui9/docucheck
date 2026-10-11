# DocuCheck 开发评测与验收工具

> **当前入口（2026-10-11）**：品目对应 C 的指定合成闭环已完成，见[当前评估边界](../docs/evaluation.md)与[C 报告](../docs/document-item-binding-c-acceptance-2026-10-11.md)。新动态评估 D1 已准备[协议](../docs/superpowers/specs/2026-10-11-dynamic-evaluation-design.md)、[三大步计划](../docs/superpowers/plans/2026-10-11-dynamic-evaluation.md)、[12包/104格蓝图](dynamic/case_matrix.json)和[核实来源](dynamic/source_catalog.json)；尚无新原件/逐格gold/模型成绩，独立封存0。下方2026-10-03的“最新”“当前”及第1–6项只适用于旧八字段阶段，不能作为今天的执行状态。`m5_metrics`/`candidate_corpus`保持历史用途，新动态评分将在`evals/dynamic/`实现。

> **最新源码联调（2026-10-03）**：第 1 项完成。独立 MySQL/Redis 栈与重建的 worker 已匹配当前后端源码；22 项服务测试全部通过、无跳过。真实开发质量 12/12 包、104/104 格、89/89 必要证据，危险满足 0、引用错误 0/244；固定五文件性能 10/10 达标、最高 62.125 秒。修复 Case 列表升版显示，前端最终 19 项、类型检查及构建通过；真实浏览器矩阵/导出、Bob 隔离、上传/修正/历史已核对。本轮上报 293 次模型调用，实际金额未知；独立人工封存仍 0。见 [本轮报告](../docs/m5-current-version-integration-2026-10-03.md)。第 2–6 项仍待逐项执行；以下为历史记录。

> **交付后复查最新（2026-10-03）**：确认并修复 7 项代码问题，新增 41 项回归；原 Conda 和依赖锁对应的 Windows 隔离环境均 **765 passed、22 skipped**，网络尝试 0，pip check/Ruff 通过。包括历史规则结果读取/追问、八字段内存事实修正、混合版本正式标记、归档声明和离线边界。当前六个业务文件已修改，M5-B 真实模型成绩仍绑定原源码；本轮真实模型调用 0。新归档恢复 228 文件逐字节一致，资料错误 0；独立人工封存仍 0，生产/远程 CI 等条件保留。见 [复查报告](../docs/m5-post-delivery-review-2026-10-03.md)。下方按日期保留历史结果。

> **M5-C 首次交付历史（2026-10-03）**：完整依赖锁、离线网络保护与 GitHub Actions、开发证据归档/恢复、启动和面试手册已交付。原 Conda、Windows 新环境、Linux 新环境各为 **724 passed、22 skipped**，网络尝试均为 0；前端 **18 passed**、类型检查/构建通过。最终 ZIP 的 227 项载荷已恢复并重验，12 包/104 格/38 原件无资产错误。业务源码与 M5-B 最终实测版相同，本轮新增真实模型调用为 0。独立人工封存仍为 0、实际账单未知；远程 CI、业务数据库灾备及完整生产发布尚未验收。见 [M5-C 交付报告](../docs/m5-c-delivery-2026-10-03.md)、[复现手册](../docs/reproduction.md)、[十分钟演示](../docs/interview-demo.md)。下方较早阶段、测试数字和暂停点均按其日期理解。

> **M5-B历史（2026-10-03，进入 M5-C 前）**：最终fields-v4源码/worker同步，12包任务12/12、状态104/104、必要证据89/89、危险满足0、引用错误0/247；固定五文件性能9/10成功且≤120秒，第5次执行中断保留失败，跨两天恢复仅继续未开始任务。额外追问/取消/未知供应商/同源修正4项通过，修正139.44ms；最近完整后端690 passed、22 skipped，收尾检查通过。AI复核且作者已见，独立封存0、实际费用未知；该时点完成范围是M5-B开发验证，M5-C当时尚未开始。下方按日期保留历史结果和暂停点；它们不撤销用户已委托的AI复核与后续执行授权。见 [最终报告](../docs/m5-b-development-validation-2026-10-03.md)和 [可移交汇总](reports/m5-b-development-2026-10-03.json)。

## M5-C 开发证据归档与恢复

`delivery_bundle` 默认只预览，不读 `.env`、连接数据库或调用模型。白名单包含 M5 合成原件、拟定 gold、AI 复核和全部整轮质量/最终性能及失败记录、依赖锁与交付手册；不包含真实上传目录、身份导入、私有 CA 或数据库。缺文件、哈希变化、危险 ZIP 条目、敏感内容和已有输出均拒绝。

```powershell
# preview/build 仅由持有原始报告与对应实测源码的开发机执行
$measuredRepo = 'data/m5-c-recheck/source-for-rechecked-bundle'
python.exe -X utf8 -m evals.delivery_bundle --repo $measuredRepo
$bundleOutput = Join-Path 'data' ('m5-delivery-' + [guid]::NewGuid().ToString('N') + '.zip')
python.exe -X utf8 -m evals.delivery_bundle --repo $measuredRepo --build --output $bundleOutput
# 接收机器从取得的 ZIP 开始；修改为实际文件路径
$archivePath = 'data/m5-c-recheck/docucheck-m5-development.zip'
python.exe -X utf8 -m evals.delivery_bundle --verify $archivePath
# restore 必须选择尚不存在的目录
$restoreDir = Join-Path 'data' ('m5-restored-' + [guid]::NewGuid().ToString('N'))
python.exe -X utf8 -m evals.delivery_bundle --restore $archivePath --destination $restoreDir
python.exe -X utf8 -m evals.candidate_corpus --root (Join-Path $restoreDir 'data/m5-candidates') --output (Join-Path $restoreDir 'restored-corpus-validation.json')
```

最终归档已实际验证与恢复：227项载荷，含清单共228项；12包/104格/38原件完整性错误0。SHA、原始验证记录及边界见[M5-C交付报告](../docs/m5-c-delivery-2026-10-03.md)。该ZIP是开发证据包，源码和M5-C报告单独保留；AI复核、作者已见、独立人工封存0，不生成或伪造 `freeze.json`。

交付后复查的归档 SHA、228 文件字节核对及新源码的 765 项测试见[复查报告](../docs/m5-post-delivery-review-2026-10-03.md)。当前工作区直接 preview/build 旧实测包会因源码变化拒绝；`$measuredRepo` 是原开发机保留的资料目录，不随 Git 克隆自动产生，接收者无需此目录。

## M5-B 历史：AI委托复核与正式上传实测

用户于2026-10-02明确要求由AI核对并继续。`acceptance/development_review_2026_10_02.json`记录已核对的104格，原件与gold不变；属性是AI复核、作者已见、独立封存0，不伪造人类签署。开发验证可继续；下方人类冻结工具是正式独立验收的另一条路径。

`m5_metrics`只在评分侧读参考答案与原件，固定失败分母，分别记录必要证据、危险满足、引用及任务；`m5_run`默认离线预览，显式发送时只上传inputs原件和问题。首次运行、失败、未知用量、超时取消均留档；不自动retry/resume。计分器不执行产品规则生成答案。

```powershell
# 选择新输出目录；默认不读.env或令牌，也不访问API
python.exe -X utf8 -m evals.m5_run --pack m5-c02 --output data/m5-next-single-preview
python.exe -X utf8 -m evals.m5_run --output data/m5-next-quality-preview
python.exe -X utf8 -m evals.m5_run --performance --output data/m5-next-performance-preview
```

`--send`才访问本机API并可能产生模型费用，需要已运行且版本对应的OIDC、API、解析和worker；OIDC令牌只从`DOCUCHECK_EVAL_TOKEN`环境变量读取，不写进参数或报告。运行前核对本地预算和发送范围，不把预览或脚本模型测量当作真实业务成功。

真实首轮12包/104格已执行：状态47/104、必要证据44/89，严格任务5/12；38/38当前输入原件解析完整。失败仍占固定分母。相邻校验修复后新两供应商包16/16，固定v2五文件性能10/10、160/160状态、150/150必要证据；这些不替换首轮质量分数。该早期记录运行服务已同步fields-v3，后端626 passed、22 skipped；v3完整开发复测及十次性能未测。详见 [最新传输、校准及验证记录](../docs/m5-b-transport-and-calibration-2026-10-02.md)。费用及外部缓存分类未知，正式独立验收仍未完成。

## M5-A 独立人工复核与冻结校验

2026-10-02补齐 [版本绑定复核工具](../docs/m5-a-review-gate-2026-10-02.md)。实际新目录 `data/m5-review/` 已准备，104格全部pending，reviewed0/sealed0；旧 `data/m5-candidates/review/` 的表格保持草稿。新CSV加入reviewed_status，确认JSON保留哈希、六包留出范围和待填的人类声明；不会自动填人名或把“继续”当逐格复核。

```powershell
# 默认只检查，当前未填写会返回ready_to_freeze=false
python.exe -X utf8 -m evals.corpus_review

# 新资料/标签版本要重新准备到全新目录，不能覆盖旧复核
python.exe -X utf8 -m evals.corpus_review --prepare --review-dir data/m5-review-v2
```

只有真实逐格核对、版本一致和用户明确确认后才执行 `python.exe -X utf8 -m evals.corpus_review --freeze`，然后用 `--seal data/m5-review/freeze.json` 重核。当前冻结尝试被review_not_ready拒绝，未产生实际冻结记录；测试成功路径只在临时目录使用虚构身份，不是人类验收。工具离线，不读.env或调用模型/数据库/解析进程；本机冻结声明不是数字签名或身份认证。状态和gold不符时先修订gold与清单，再重新独立复核，不能自动更新哈希接受旧确认。

## M5-A 新合成候选（待人工复核）

用户要求继续检索GitHub/社区；检查结果及替代方案见 [候选准备记录](../docs/m5-a-candidate-corpus-2026-10-01.md)。`acceptance/candidate_blueprints.py`只提供新原文，`candidate_gold.json`是Agent人工拟定答案，不调用产品规则生成。生成后12包/104格，6校准、6留出候选；作者已见全部资料，**尚未独立复核/封存；后续开发实测分数见顶部报告**。

```powershell
# 现有目录已生成；默认离线核验，绝不调用模型或读取.env
python.exe -X utf8 -m evals.candidate_corpus --output data/m5-candidates/verification.json

# 仅在需要重建时选择一个尚不存在的目录；拒绝覆盖已有资料/gold/复核记录
python.exe -X utf8 -m evals.candidate_corpus --build --root data/m5-candidates-reproduction --output data/m5-candidates-reproduction/verification.json

python.exe -m pytest backend/tests/test_candidate_corpus.py -q
```

输出`inputs/<pack-id>/manifest.json`与原始PDF/XLSX、独立`gold/<pack-id>.json`、`review/review-guide.md`及待填`review.csv`。`runtime_manifest(pack_dir)`只读取inputs；M5-B后续适配器从此上传真实原件，评分才读gold。`agent_demo --case-dir`读取开发缓存，不直接支持这批输入，不用它代替真实解析验收。

生成器使用已锁定的reportlab/openpyxl/pypdf；Windows默认SimHei，其他环境用`--font /absolute/path/chinese.ttf`，测试用`DOCUCHECK_CJK_FONT`。字体哈希和依赖版本进入清单，跨环境改变需重新核验；缺字体明确skip，不能将其报成CI通过。默认工具始终报告reviewed=0/sealed=0，不签署人工封存；确认模板不是签名。状态、原文及范围的独立人工复核完成后，需单独更新并确认冻结哈希，不能仅点“继续”当作已复核。

## M5 公开来源准备（尚未封存）

M5 按 [三个大步骤](../docs/m5-large-step-plan-2026-10-01.md)进行。用户要求先找公开资料；已搜集的来源、限制和回归证据见 [资料调查](../docs/m5-a-public-source-research-2026-10-01.md)。`acceptance/source_catalog.json` 是原件来源目录，尚不是独立业务标注集。7份原件保存在被Git忽略的 `data/m5-source-research/`。

```powershell
# 离线检查已下载原件；没有模型、数据库或Docker操作
python.exe -m evals.source_assets --output data/m5-source-research/asset-verification.json

# 在新目录从公开网站重建；显式联网，来源内容变动或下载失败会报错
python.exe -m evals.source_assets --download --root data/m5-source-reproduction --output data/m5-source-research/reproduction-final-verification.json

python.exe -m pytest backend/tests/test_acceptance_source_assets.py -q
```

工具不会覆盖已被修改的原件；SHA-256不符时先核对来源，不自动更新目录中的预期值。原件保存在被忽略的data目录，来源目录和校验命令用于重建与追溯。长PDF及ZIP超出当前上传范围，不能把资产校验成功当作业务验收通过。人工标注、资料分组和独立封存仍需完成。

## M2 合成开发集

这里是可重复运行的**合成开发集**，与 `examples/dev-pack-001/labels.json` 分开。`cases/lexical-001.json` 的 `documents[].blocks` 构成检索输入，`queries[].relevant` 只在检索结束后计分；标注不会进入证据正文、检索查询扩展或 Agent 提示。此集只用于开发，不能代替 M5 的独立封存验收集。

在已有的 `docucheck` Conda 环境中，从仓库根目录运行：

```powershell
python.exe -m evals.m2_development --output data/evals/m2-lexical-001.json
python.exe -m evals.m2_agent --output data/evals/m2-agent-scripted.json
python.exe -m pytest backend/tests/test_m2_development_eval.py -q
```

第一个命令只读取合成 JSON，在本机调用现有词法检索和规则边界；不会调用模型 API、Docker 或读取 `.env`。不加 `--output` 时将 JSON 报告打印到终端。报告保留每个查询的必要来源分子/分母、漏召回位置、跨供应商引用数。无命中查询的必要来源分母为 0，不计入总体召回率。`guardrails` 是三个离线边界探针的通过数，**不是**真实 Agent 的无效引用率或业务正确率。

2026-09-27 的基线：增加字段别名之前，8 个有标注查询的必要来源召回为 **14/17**，漏掉“显示屏对角线”与“屏幕尺寸”、“保修期”与“质保期限”、“交货”与“交付期限”三组对应来源。加入词法别名后的同一数据为 **17/17**；另有 1 个无命中查询，供应商隔离 **9/9**，无效引用拒绝、部分覆盖不发布正式状态、无命中不当作字段缺失各 **1/1**。这些小样本数字只用于追踪本次检索改动，不能推断真实文件上的召回率。

`m2_agent.py` 读取经哈希核验的单家和三家合成包，使用**脚本化 Provider**驱动真实 LangGraph、抽取、规则和答复链路，不联网。当前开发结果是 Agent 任务 2/2、状态 32/32、来源范围错误 0/76；模型调用分别为 9 与 23。报告的 `scripted_usage` 是测试桩写入的计数值，不能当作 DeepSeek token、费用或真实性能。真实模型任务成功率、调用数、token 和延迟须由下方用户探针另行记录。

## M2-9 真实 DeepSeek 探针（由用户执行）

完成八字段 Agent 和 CLI 回归后，用户在本机已配置的 `docucheck` 环境运行一供应商的合成包：

```powershell
python.exe -m backend.app.agent_demo --case-dir examples/dev-pack-001 --provider configured --mode thorough --question "请完整审查供应商 A 的八项指标，并列出每项结果与来源" --output data/m2-agent-demo
```

单家通过后，三供应商使用已构建并标注的 `examples/dev-pack-003` 合成包：

```powershell
python.exe -m backend.app.agent_demo --case-dir examples/dev-pack-003 --provider configured --mode thorough --question "请完整审查全部供应商的八项指标，并列出每家每项结果与来源" --output data/m2-agent-demo
```

这两条命令会向已配置的 DeepSeek 服务发送问题、工具定义及 Agent 选取的合成资料原文片段，可能产生多次计费请求；不会发送 `.env` 或原始 PDF/XLSX 二进制文件。运行前确认模型账户可用。请反馈终端状态、八项/24 项结果、模型调用数和脱敏后的失败码；不发送 API key、`.env` 或完整请求日志。若返回 `partial`、`failed` 或 `waiting_user`，保留记录并先定位原因，不连续盲重试。三家资料数值刻意相同，只检验 24 格及跨家引用；没有用户运行回执时，不得把 M2-9 标为真实验收通过。
