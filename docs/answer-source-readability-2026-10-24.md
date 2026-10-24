# 答复与来源展示交付

聊天现在先呈现结论，来源显示编号、文件名和页码/单元格，前三处直接可见，其余可展开；整理过的答复保留“查看原始答复”。沿用当前样式与原文侧栏，不修改正式状态、历史答复、后端规则、协议或导出。

![当前苹果报价的结论、来源与原始答复入口](assets/answer-source-readability-2026-10-24.png)

截图来自本机已保存的合成苹果报价结果：数量满足，单价因税说明矛盾为资料冲突。它展示读取与来源入口，不证明任意采购资料质量。

## 实现与边界

源码提交 `e1fb25c9c04f0776835b88153bb988966351cd57`，涉及 [展示函数](../frontend/src/answerDisplay.ts)、[答复组件](../frontend/src/components/AssistantAnswer.vue)、聊天接入及对应测试/样式，共 7 个文件。仅在服务端来源格式与可信 JSON export 的完整 source_text 对应时精简尾段；自由文本、残缺或无法验证的内容保留全文。兼容服务端按 Unicode code point 截取 600 字的引用。

正式来源只读复用已有 JSON export，并核对 Run、Case、snapshot 与 revision。相同 evidence_id 合用一个入口但保留全部引用编号和不同摘录，不按文件名合并不同证据。20 秒超时涵盖 token 获取与 fetch；切换范围/卸载取消，迟到结果不回写；读取失败保留原答复并可重试，权限错误交回既有父级守卫。

前端读取旧 Run 时可以整理显示，但持久化答复与旧版本结果保持原值。“完整报告”和 JSON / Excel 继续读取相同后端结果，不因精简显示丢失引用。

## 验证

- 前端完整 **324 tests / 20 files passed**，`vue-tsc --noEmit` 与 Vite 构建通过，最终 `git diff --check` 通过。使用既有 ignored runner 放行 Junction 指向的本机依赖，未改产品 Vite 配置或安装依赖。
- 只读复审发现并关闭两个 P2：同一 evidence_id 去重丢失后续编号/不同 quote；仅 abort fetch 无法限制挂起 token。红测各 3 项失败，修复后 64 项相关测试及完整 324 项通过。复审用真实 apiRequest 和挂起 token 验证超时退出、迟到 token 后 fetch 仍为 0；最终没有剩余实证 P1/P2。
- 真实浏览器核对 v13、v14、数量更正后三份历史结果；整理后的正文保留结论，原始答复逐字匹配只读 API，来源数分别 10/8/8，与 JSON export 一致。旧摘录问答与当前短答均检查；展开来源后点击报价!B8，原文面板显示 25 元/箱及相邻含税/不含税说明。
- 1440×1000 桌面及 390×844 窄屏检查通过，窄屏文档宽度 390，无水平溢出；原生 details 可用 click / Enter 展开。刷新并重新选择原工作区后，三份历史再次核对。
- 实际 partial 运行的历史状态与完整报告已回读（3/4 批次、10 格均非正式）；直接 review 运行在聊天中只展示状态，partial 答复整理分支由组件测试与复审覆盖。鉴权失效、超时、迟到、伪造尾段等异常主要由组件测试与复审覆盖，不声称浏览器逐一触发了所有错误路径。

首次窄屏检查有原文面板覆盖内容，只保留作中间记录；关闭面板后重新检查并截图。浏览器核对工具曾有 OpenBLAS 导入、字段名以及加载未结束的问题，修正工具/等待条件后成功；没有将这些工具失败报成产品故障或隐去后冒充一次通过。展示验收没有发送新聊天或模型请求。

## 本机应用

从固定前端提交 archive 构建并核对与受测 dist 一致，10 个实际 HTTP 文件 SHA-256 全部相同。前端运行 `e1fb25c`，API / worker 仍固定 `e5da3d2`，健康接口 200、匿名身份接口 401；源码和文档提交身份分别记录。只更新前端进程，保留旧固定快照和旧启动脚本。

本机地址为 `http://127.0.0.1:15173/`。入口 `data/verification/quality-retest-20261024/frontend-deploy/start_entries.ps1`；旧金额部署入口已转发新入口，实际执行核对成功。回退使用保存的 previous-start_entries.ps1 与旧固定前端，未做异机恢复演练。

本轮 [18 包业务复测](current-quality-retest-2026-10-24.md)另行评分，前端 324 项通过不等于业务质量通过。后端源码未变，最近完整离线仍为 [2588 passed / 62 skipped、网络尝试 0](money-tax-live-2026-10-24.md)；未为前端改动重复计入该后端测试数。GitHub CI 的最终状态以[工作流](https://github.com/YanShui9/docucheck/actions/workflows/offline-ci.yml)为准。

本机证据根目录 `data/verification/quality-retest-20261024/`：`frontend-full-02.log`、`frontend-build-02.log`、两次 display-review-red 日志、ui-desktop/expanded/source/mobile-02/refreshed 系列、`ui-acceptance-01.json`、`frontend-deploy/verified-01.json`。公开截图只截取合成答复区域。
