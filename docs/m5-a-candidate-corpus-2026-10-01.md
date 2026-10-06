# M5-A：社区检索与候选语料准备

## 本步结论

截至2026-10-01，本轮在 GitHub/Hugging Face 找到了相关项目及采购数据，但没有找到可直接复用的“中文笔记本需求＋各供应商报价＋精确规格＋八字段原文标注”配套集。这个结论只针对本轮查到并检查的来源，不代表网络上不存在此类资料。

按用户授权，现已生成 **12个全新合成候选包、13个供应商、104格拟定标签**：6个校准候选（56格），6个留出候选（48格）。共25个PDF、13个XLSX，PDF共36页；单Case最多5文件/4个PDF页，均在当前上传范围内。另保留上轮7份真实公开原件作复杂文档结构/上传边界子集，其取得过程与限制见 [公开来源调查](m5-a-public-source-research-2026-10-01.md)。两者分别报告。

**M5-A仍未正式完成：reviewed=0、sealed=0。** 拟定gold由Agent编写和核对，尚需独立人工复核。真实解析质量、DeepSeek质量、成本与端到端时延未测量；不进入M5-B。104格是候选规模，不是104格业务验收成功。

## 社区检索证据与选择

直接检查了五个GitHub仓库的API元数据、当前提交的递归文件树，以及下列数据卡。仓库目录快照记录在 `evals/acceptance/community_repository_snapshot.json`；仅作为本轮检索证据，星数会变化。没有执行外部仓库代码，也没有引入其依赖。

| 来源 | 本轮实际发现 | 如何使用 |
| --- | --- | --- |
| [amgdy/rfp-analyzer](https://github.com/amgdy/rfp-analyzer) | RFP/提案打分与多供应商比较项目；MIT；递归树98文件，未发现配套PDF/XLSX验收资料与gold。 | 参考业务链路，不当作本项目数据集。 |
| [CopelandBEC/openrfp](https://github.com/CopelandBEC/openrfp) | 类似RFP分析项目，MIT；101文件的树中未发现完整采购资料样本。 | 参考方向，不照搬实现或宣称高星。 |
| [microsoft/dstoolkit-adaptiveprompting](https://github.com/microsoft/dstoolkit-adaptiveprompting) | 有9份不同领域的RFP纯文本和索引，用于摘要/提示优化；没有供应商报价与规格配对；本轮API license=null、树中无许可文件。 | 参考分组与评价维度，不复制原文入验收集。 |
| [kundanshar-cell/procurement-benchmarks](https://github.com/kundanshar-cell/procurement-benchmarks) | 发布者说明50条采购问答，CC BY 4.0；数据是问答JSON，不是可定位原件的8字段核验包。 | 参考风险/采购问题分类；其“真实”是发布者描述，未独立核实。 |
| [18F/Head_Start_TTA_RFQ](https://github.com/18F/Head_Start_TTA_RFQ) | 实际公开RFQ及响应、价格等模板，其中有XLSX；属于服务采购和空白模板，缺真实供应商填报配对。 | 参考需求/响应文件组织；未下载改写成笔记本资料。 |
| [tcepi/bidCorpus](https://huggingface.co/datasets/tcepi/bidCorpus) | 葡语招标文本；数据卡标Apache-2.0，含分类、实体和相似性任务；gold是采购资格等标签。 | 研究参考；任务/语言/标签粒度与本项目不同，不下载11.5GB凑数量。 |
| [ProcureTap/us-government-bids-sample](https://huggingface.co/datasets/ProcureTap/us-government-bids-sample) | CC BY 4.0采购公告元数据/简介；说明简介最多2500字符，明确排除完整RFP正文。 | 无法核对PDF页码、报价与配置，不作为原件验收。 |
| [AyoubChLin/CompanyDocuments](https://huggingface.co/datasets/AyoubChLin/CompanyDocuments) | Apache-2.0，英文订单等文档及结构化抽取字段；任务是订单实体提取。 | 没有当前任务的配套需求/设备规格/符合性标签，不直接复用。 |

本轮检查的五个GitHub仓库仅0–6 stars，因此没有把它们称为高星或主流标准。是否适用于任务、来源和标签是否完整，比热度更重要。公开数据卡的许可标识也不能证明第三方内容权利或标签准确性；这次没有再分发这些社区原文。

## 新候选的覆盖

| 包 | 分组 | 供应商/判例 | 核对重点 |
| --- | --- | --- | --- |
| m5-c01 | 校准 | 2/16 | 两家参数不同；内存冲突、重量不达标、质保缺失；固定五文件性能场景 |
| m5-c02 | 校准 | 1/8 | 八字段恰好等于阈值 |
| m5-c03 | 校准 | 1/8 | 十进制TB/GB、g/kg、年/月换算 |
| m5-c04 | 校准 | 1/8 | 扩容与实装、可选配置、未税价、工作日 |
| m5-c05 | 校准 | 1/8 | 完整适用资料中内存、质保和数量缺失 |
| m5-c06 | 校准 | 1/8 | 存储、重量、质保、数量多个字段冲突 |
| m5-h01 | 留出候选 | 1/8 | 附件分页与小数阈值 |
| m5-h02 | 留出候选 | 1/8 | 八字段不达标，含一元价格差 |
| m5-h03 | 留出候选 | 1/8 | 技术规格多处缺项，商务信息完整 |
| m5-h04 | 留出候选 | 1/8 | 含电源运输重量、含附件总价、交付起点不同 |
| m5-h05 | 留出候选 | 1/8 | 跨页内存/存储冲突 |
| m5-h06 | 留出候选 | 1/8 | 无关型号、大模型应忽略的文档内恶意指令，实际三项不满足 |

拟定状态分布为：满足64、不满足18、资料冲突7、证据不足8、待人工确认7。所有gold处理状态是`complete`，表示这些完整候选原件的预期可处理状态；以后系统返回partial/failed必须保留并按固定协议计分，不修改标签迎合结果。

仅有两个版式家族：校准`memo-v1`，留出`annex-v2`，不跨分组。句子仍比较规整，不能代替扫描、复杂跨表、真实多型号及多语种采购覆盖；原始长PDF/复杂公开表格另列边界资料，未把它们计入这104格。两供应商用例不代表三供应商新独立验收通过。若面试展示泛化能力，应继续取得更多真实匹配资料及不同版式，分别公布测量结果。

## 实现与答案隔离

`evals/acceptance/candidate_blueprints.py`只设计原文，`candidate_gold.json`显式编写拟定状态/理由。生成器不导入产品核验规则、不调用Agent来产生“标准答案”。

```text
data/m5-candidates/
  corpus.json                 分组、资料/gold哈希、设计/字体/依赖版本
  inputs/<pack-id>/           manifest.json + 原始PDF/XLSX
  gold/<pack-id>.json         八字段拟定状态、必要原文定位、缺失检查范围
  review/review-guide.md      104格人类可读工作表，链接原件
  review/review.csv           逐格接受/修改、真实复核人/日期/意见（均待填）
  review/attestation-template.json  待填确认模板，不是封存证明
  verification.json          本机资产/标签定位报告
  previews/                  本机PDF渲染检查页
```

`runtime_manifest(pack_dir)`只读单包输入清单及原件哈希，不访问gold或复核目录；已通过把单个inputs包复制到没有gold的目录的测试。它是M5-B执行适配器的输入契约，尚未将这批原件上传正式Case或运行Agent。现有`agent_demo --case-dir`面向开发解析缓存，不能直接拿该候选目录冒充真实解析验收。

校验工具覆盖哈希/大小、PDF结构与总页数、实际XLSX解析、八字段/型号/供应商、原文所在PDF页或精确sheet/cell、冲突双方、缺失的完整quote/spec范围、家族划分、与现有开发原件完全重复和路径逃逸。PDF原文比较仅忽略排版空白；XLSX要求指定单元格原文完全一致。校验不证明拟定状态正确，也不自动认证某人独立复核。哈希用于发现变更，不能当安全签名或标注正确性的证明。

生成只写全新目录，拒绝覆盖已有目录。字体未复制进Git，使用本机SimHei，并登记字体SHA-256；跨机器需要合法可用的中文TrueType字体和固定依赖，字节差异需重新复核。缺字体时本组生成测试明确skip，M5-C的CI必须配置字体，不能把skip当运行成功。

## 运行与核验

在`docucheck`环境中，从项目根目录运行；当前目录已经生成，无需再次执行`--build`。

```powershell
# 离线核验已有候选；不读取.env，不调用模型/数据库/Docker
python.exe -X utf8 -m evals.candidate_corpus --output data/m5-candidates/verification.json

# 需要重现时使用一个尚不存在的新目录，保留原复核文件
python.exe -X utf8 -m evals.candidate_corpus --build --root data/m5-candidates-reproduction --output data/m5-candidates-reproduction/verification.json

python.exe -m pytest backend/tests/test_candidate_corpus.py -q
```

非Windows环境可加`--font /absolute/path/chinese.ttf`，生成测试通过`DOCUCHECK_CJK_FONT`指定同一字体。不得自动覆盖已修改的gold或复核记录。

本轮定向测试 **21 passed**：先观察缺实现失败，再覆盖生成一致性、原件/定位/范围/标签篡改、数量不足、清单结构错误、损坏PDF、输入答案隔离和真实上传预检。自查发现`gold`父目录链接可能逃逸，加入真实目录链接测试后修复为以整个语料根目录为边界。最终同环境重建的原件、gold、清单与复核模板逐文件字节一致。

实际`UploadStorage.save`对38个文件全部接受，哈希一致；13个XLSX经现有解析器均为complete。25个PDF的36页全部用Poppler渲染，查看6张覆盖全部页的联系表，文字、页眉页脚和分页未发现缺字、重叠或裁切。这里只证明结构与排版检查，不代表实时MinerU解析通过。

后端全量 `python.exe -m pytest backend/tests -q`：**496 passed, 22 skipped，83.13秒**。`ruff check backend evals`、`pip check`、`git diff --check`通过；只有已有LF/CRLF转换提示。外部MySQL/模型/实时MinerU集成未运行，没有启动Docker或前端。候选报告 `data/m5-candidates/verification.json` 无错误，公开原件重核仍7/7通过、1条未取得。另用CLI在全新 `data/m5-candidates-reproduction/` 重建：inputs/gold/review与corpus清单共 **66个规范文件逐字节相同**，差异0，报告为 `reproduction-check.json`；其corpus SHA-256为 `90d59a51238a9288b2f9724a3b9cf2a6548a7111c031faf0b0b40aed75751f29`。Git基线HEAD为`efcc62ab25e9e06dc4b4bba4a16d04fcfcd8c867`，工作区含先前未提交改动；HEAD不是本轮完整实现快照，不得作为最终验收唯一版本标识。

## 下一步：独立人工复核与封存

打开本机 `data/m5-candidates/review/review-guide.md`，每格都有拟定状态、理由和原件链接；无需启动服务。完整检查供应商与型号、需求比较词、实装/最大/可选、税和计价单位、交付起点及日历口径；冲突查看双方；缺失查看该供应商完整两份原件。填写`review.csv`的`review_decision`（accepted/changed）、真实复核人、日期与意见。仅回复“继续”或同意方案不当作已逐格复核。

有修改时先修正gold、核对全部必要来源，再更新经人工确认的清单哈希并重新校验；生成器不原地重建覆盖。随后记录确认模板里的代码实现快照、corpus哈希和使用历史，由用户明确确认至少六包冻结、没有拿其真实模型结果调参。这一步尚未执行，也没有自动填写姓名或签署封存。

作者已见过全部合成资料，不能宣称是第三方从未见过的数据。若需要严格盲测，用户应提供/改写新资料，或由独立人员另出一批并持有gold；若已按留出结果改规则或提示，原留出只能转校准并补新资料。正式封存后任何原件、gold、分组变更使旧确认失效。正式测量门槛与失败分母仍按 [M5三步计划](m5-large-step-plan-2026-10-01.md)执行。
