# M5-A 复核及封存校验交付

日期：2026-10-02。本步仍属于M5-A，候选准备见 [10月1日记录](m5-a-candidate-corpus-2026-10-01.md)。用户授权继续工作，没有提交独立人工复核结果，因此本轮没有认证或冻结实际资料。

## 做了什么

1. 检查了既有生成器、原件与拟定gold边界。发现清单为`null`或documents里混入字符串时会抛`AttributeError`，补两个先失败后通过的用例，修为`input_schema`结构化报告；不修改原件、gold、产品规则或提示。
2. 新增 `evals/corpus_review.py`：准备空白复核目录、检查104格覆盖与逐格状态/姓名/日期、绑定版本及污染历史；明确确认后才允许独占写冻结记录。现有 `evals.candidate_corpus` 继续只报告资产完整性，不能单独认证人工复核。
3. 将完整候选版本和当前backend/evals Python源码、依赖配置的SHA-256记录在snapshot中，排除.env。HEAD存在未提交实现时，仅记录HEAD不够，所以使用源文件哈希基线；它是版本指纹，并非代码归档或已提交Git版本。后续实际测量仍另记执行版本。
4. 补齐错误与变更边界：漏格/重复/额外格、未确认、不同复核人、无效/未来日期、状态不符、修改标签但未改gold、使用留出集调参、原件/CSV/确认/快照/冻结记录变化、封存路径不在复核目录、覆盖已有记录均拒绝。
5. 实际准备 `data/m5-review/`，所有104格为pending；只在临时测试目录用“Fixture reviewer (not real)”检查冻结成功路径。实际目录的封存尝试被`review_not_ready`拒绝，没有freeze.json。

版本管理参考 [LangSmith数据集管理](https://docs.langchain.com/langsmith/manage-datasets)关于明确版本与分组的做法；人工声明/资产校验/模型效果分开记录，参考 [评测类型](https://docs.langchain.com/langsmith/evaluation-types)。本步只用现有依赖和本地文件，不接入LangSmith云平台或额外模型。

## 数据流与边界

```text
原件 + 输入清单 + 拟定gold
  → 原件/定位/范围校验
  → 全新复核目录（104空白逐格确认 + 版本快照）
  → 真实人核对原文并填写声明
  → 逐格/声明/版本校验
  → 显式冻结 → 以后每次测量前重核冻结记录
```

`review.csv`中的proposed_status不能改来迎合输出。独立核对后填写reviewed_status；如果与gold不同，先反馈原文和建议状态，修正gold、必要定位与清单版本并重新准备复核。工具不自动更新期望哈希或复用旧确认来接受新标签。

`confirmation.json`要求state=confirmed、真实复核人/日期与每格一致、independent_human_review=true、author_seen_acknowledged=true、holdout_used_for_tuning=false，且如实填写contamination_history。留出清单必须精确为该候选版本的六包，不能替换为校准包，不能漏格后冻结。未填写的确认字段是null/空，不把默认false当作人声明“没有调参”。

freeze.json绑定corpus、snapshot、CSV和确认文件的哈希及复核声明。校验时重新检查原件与gold定位，再比较冻结记录的完整规范字节；仅重新格式化文件也使旧记录失效。读取指定seal文件必须位于复核目录，不静默改读同名文件。记录创建使用exclusive写入，拒绝覆盖。

**这不是身份认证或数字签名。** 工具能证明文件版本一致、声明完整，不能证明填表者是真的人、真的独立或标注正确；能同时恶意改写全部文件的人仍可伪造本地声明。实际审阅与用户确认不能省略，identity_authenticated=false始终明确报告。作者已见全部合成资料，不能宣称第三方未见盲测或真实项目泛化。M5-A的gold生成时pending标记保留为来源记录，复核后的批准状态由外部版本绑定的冻结记录表达，不通过篡改gold自身的生成标记来改变结论。

## 你需要使用的文件

当前新目录：

- `data/m5-review/README.md`：实际操作说明，链接104格原文定位工作表。
- `data/m5-review/review.csv`：待填写的逐格复核，新增reviewed_status列。
- `data/m5-review/confirmation.json`：待填写的人类确认及使用历史；保留版本哈希。
- `data/m5-review/snapshot.json`：已固定的资料版本和源码指纹。
- `data/m5-review/check.json`：本轮实际检查结果，pending104/reviewed0/sealed0。

上一轮 `data/m5-candidates/review/` 中的表格是初版草稿说明；本轮使用新的版本绑定目录，不覆盖旧文件。原文工作表仍可查看，初版attestation-template.json不作为新工具的确认入口。

## 命令

已准备好实际目录，你现在无需重建或启动服务。检查已有复核：

```powershell
python.exe -X utf8 -m evals.corpus_review
```

准备新版本时使用全新目录，拒绝覆盖旧复核：

```powershell
python.exe -X utf8 -m evals.corpus_review --prepare --review-dir data/m5-review-v2
```

下面两条仅在真实复核完成、用户明确确认冻结之后执行，本轮没有成功执行实际冻结：

```powershell
python.exe -X utf8 -m evals.corpus_review --freeze
python.exe -X utf8 -m evals.corpus_review --seal data/m5-review/freeze.json
```

准备与检查不调用模型、数据库、MinerU、Docker或前端，也不读取.env。pending报告正常退出但ready_to_freeze=false，不能当作封存成功；拒绝冻结及损坏版本的CLI退出1。

## 本轮验证与剩余工作

| 项目 | 实际结果 |
| --- | --- |
| 新复核工具测试 | 20项通过；真实成功冻结仅使用临时虚构复核 |
| 候选校验 | 原21项＋新2项，共23项通过；两类错误清单不再抛异常 |
| 定向合计 | 43 passed，41.37秒（后续全量亦包含全部43项） |
| 后端全量 | 518 passed、22 skipped，94.84秒；跳过的外部集成没有运行 |
| Ruff / 依赖 | `ruff check backend evals`和`pip check`通过，无新依赖安装 |
| 实际资料 | 候选资产错误0；新复核目录pending104、reviewed0、sealed0；未生成freeze.json |

实际业务质量、费用和时延仍未测量；本轮没有调用DeepSeek或实时MinerU，没有操作Docker或前端。M5-A的代码/工具准备已补齐，但人工逐格复核与至少六包确认冻结未完成，**M5-A整体仍未验收**。用户已收到复核安排问题；选择“我来复核”表示开始安排，不能代替实际填写与核对。下一步根据真实复核反馈修正标签并重新检查，满足冻结条件后才进入M5-B。
