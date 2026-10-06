# M5-B 传输与交付上限修复计划

> 按用户已经批准的 M5-B 继续实施；在当前会话直接执行、测试和审查，不另设用户操作门槛。内部回归不扩展为新的阶段。

**目标**：解决真实上传校准预跑暴露的 HTTPS 信任缺失，以及明确交付期限上限被误判待确认的问题；保留首次失败和运行版本。

**架构**：环境证书问题使用本机 Docker worker 覆盖配置。业务修复只在八字段确定性规则中支持交付期限的明确上限，抽取仍保留原文比较词，其他字段的非等值承诺保持待确认。规则版本改变，历史结果不回写。

**技术栈**：PowerShell / Windows 根证书库、Docker Compose、Python 3.12、LangChain/DeepSeek、pytest、SQLAlchemy。

## A. 证书传输验证

- [x] 空 chat 请求不提供 model/messages，不触发推理；冷 fork 与独立进程均复现 `SSLCertVerificationError`，verify_code=19。旧 HTTPX 和 HTTPX2 均出现，不能仅归因于 fork 或 SDK 版本。
- [x] TLS 握手链根证书为本机 Kaspersky；与 `Cert:/CurrentUser/Root` 已受信任证书 SHA-256 完全相同。只导出公开证书，不取私钥，不从网络下载后自动信任。
- [x] 将容器原有公有根证书与已核对的本机根证书合并。`data/m5-local-tls/compose.tls.yaml` 给 worker 只读挂载，并设置 `SSL_CERT_FILE`；不关闭校验，不修改 Windows/系统防护配置。
- [x] 活跃 Run 为0后单独重建 worker 容器。无采购资料的 SDK 探针、冷 fork 探针均正常收到预期422；记录环境指纹和对照观察。
- [x] 新 Case 的 m5-c02 实时解析与真实 Agent Run 完成：三原件 complete、15模型请求、状态7/8、必要证据8/8。交期仍误判待确认，失败报告不覆盖。

## B. 交付期限上限的局部修复

修改 `backend/app/rules/fields.py`、`backend/app/agent/state.py`；测试新增 `backend/tests/test_m5_delivery_bounds.py`，现有会话测试更新实际规则版本断言。

比较契约：需求与供应商均经原文验证，快照、供应商、型号、完整覆盖、日期起点和单位全部一致。只允许交期上限 `lt/lte`；供应商上限必须全部落在需求允许范围内才能判满足。例如要求≤9，承诺≤9或<9都满足；要求<9而承诺≤9需复核；承诺≤10相对要求≤9需复核，不能当作已确定超过期限。等值承诺沿用原比较。多个不一致上限或可同时成立的等值/上限混合需复核；多个不一致等值、等值落在另一来源上限之外为资料冲突，原因引用包括双方。

实现比较条件：

```python
def _delivery_upper_bound_meets(required, offered):
    return required.comparator in {"lt", "lte"} and (
        offered.value < required.value
        or (offered.value == required.value
            and (required.comparator == "lte" or offered.comparator == "lt"))
    )
```

- [x] 先写并运行端到端原文抽取到规则的离线回归。覆盖同/更紧/更松的上限、严格/非严格端点、工作日/自然日、起点不一致、近似、不同承诺及其他字段的非等值边界。预期新满足路径在旧规则下失败。
- [x] 最小修改：交期 `lt/lte` 可进入规则，完整包含判满足，其余待确认；`RULE_VERSION` 升为 fields-v2，Agent 上下文使用同一常量，历史会话中的旧判定须重核。
- [x] 执行新回归、字段/会话回归、Ruff及完整离线回归；对第一次真实完成预跑的原始事实离线重算，记录新规则下结果，不回写数据库或原报告。
- [x] 告知 worker 镜像重建/服务同步及下一次限定真实模型调用，使用新 Case 与新输出目录复验。新 m5-c02 Run 为 a1f9537c7a7c41278fde2ab7d3e8a018，8/8 状态与必要证据一致；单包成功不代表全集通过。

复审追加的升级边界：`CorrectionRecheck.load_for_run` 对既有 fields-v1 人工修正记录不能只比较新版本的完整结果，否则当前修正快照永久拒绝新 Run。保存一条显式的 v1 规则重放入口（只用于审计验证），先按历史规则重新验证全部原件/事实/映射/结果与旧记录一致，再为本次 Run 返回 v2 现算结果。原审计不回写；未知版本和篡改继续拒绝。修改 `backend/app/db/correction_recheck.py`，新增 `backend/tests/test_corrected_agent_run.py` 中旧记录升级与篡改回归，先复现失败再修复。

执行命令：

```powershell
python.exe -X utf8 -m pytest backend/tests/test_m5_delivery_bounds.py -q
python.exe -X utf8 -m pytest backend/tests/test_field_rules.py backend/tests/test_field_extraction.py backend/tests/test_session_memory.py -q
python.exe -m ruff check backend evals
python.exe -X utf8 -m pytest backend/tests -q
```

整组质量/性能仍按原固定协议；本次修复使用校准包，不改变 gold。所有结论为作者已见、AI复核开发验证，独立封存仍0。不得把单包完成等同于 M5-B 或生产验收完成。

## C. 固定质量首轮与诊断补齐

- [x] 12包/104格、38原件首轮真实上传全部执行，协议先于上传写入，没有自动重试；原报告保留。严格任务5/12，状态47/104，必要证据44/89，危险满足0，未通过门槛。
- [x] 发现失败细节未落库，增加有白名单、长度上限及隐私过滤的 Agent 模型/工具事件。与 Run/attempt 结束同事务写入，旧领取者被拒绝。完整后端603 passed、22 skipped；只读复审确认。
- [x] 新 m5-c01 诊断预跑实际写入 MySQL，定位到 fact_field；未保存模型候选原文，不能进一步断言具体是哪一个候选。

## D. 已复现的相邻单元格与检索缺陷

本段属于 M5-B 校准，不增加新阶段或更改参考标签。仅接纳同一行、紧邻、同文档/角色/供应商/型号的完整内存标签和值，右侧必须是单一内存容量，原文限定语仍验证。SSD 同义词只影响检索排序，不提供事实或判定覆盖。

- [x] 先写回归，修复前9 failed；修复后内存校验及检索47 passed。跨容量、多个值、最大支持等边界仍拒绝。
- [x] 完整回归及只读复审：618 passed、22 skipped，147.72秒；原首轮快照相同查询的检索207/207，旧报告195/207保留。
- [x] 同步 worker 与 API，限定新 Case 的 m5-c01 实测，保留诊断失败历史。新Run78836fa33cd24e5d9e32f858eece6f2b为16/16、必要证据15/15、引用0/39，上传到结果57.672秒。
- [x] 整理本轮版本、质量失败、验证与未测项，保持 M5-B 进行中；交付记录和六处当前项目入口已更新。

## E. 用户确认的采购交期承诺口径

用户于本轮明确选择“核验供应商承诺，判不满足”。需求≤20而承诺≤21不覆盖采购要求，即使将来可能提前送到也不能通过承诺审查。参考标签不修改。

`fields-v3`只在需求与报价均明确上限、同单位/起点/型号及完整校验时，将单一一致的更松上限判不满足；严格端点也按集合包含核验。多个不同承诺、未知/近似/不同起点或单位继续人工确认或冲突；其他字段不扩展。保留真正v1/v2历史规则用于审计验证，历史会话不可作为当前判定直接复用。

- [x] 补回归，旧代码出现10 failed、46 passed，包含承诺上限、v2历史语义、旧修正审计及会话版本。
- [x] 等当前10次fields-v2性能组结束后修改应用源码，保持整组版本一致。固定性能10/10、160/160状态、150/150必要证据，51.688–57.281秒。
- [x] 最小实施v3与v1/v2审计兼容，定向56 passed，完整626 passed、22 skipped，157.22秒；MySQL修正1 passed、1.80秒。只读复审另外29项通过，无可操作P0/P1/P2。
- [x] 对首轮已观察的h02原件及事实重新执行来源验证，确认实际v2结果ID与存档一致，v3由待确认变不满足；记录为离线重算、0模型请求、无新Run，不算真实复测。历史标签及原分数不覆盖。
- [x] 告知并同步运行服务到v3，验证宿主/worker75文件哈希相同、CA不变、worker健康、API公开文档200与受保护接口无认证401；此处不额外发送重复单包模型请求，下一次完整开发复测另立固定协议。
