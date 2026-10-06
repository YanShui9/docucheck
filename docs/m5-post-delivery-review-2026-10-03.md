# M5 交付后代码复查与修复

日期：2026-10-03。用户要求“再检查检查”。本轮重点复查 Agent 会话恢复、事实修正、历史读取/导出、离线验证和开发证据交付，修复实际复现的问题。

**本轮确认的 7 项代码问题已关闭，新增 41 项回归测试。** 最终源码在原 Conda 和依赖锁对应的 Windows 隔离环境中，各通过 **765 项测试，22 项外部服务测试跳过**；pip check、Ruff 通过，网络尝试均为 0。机器记录见 [复查汇总](../evals/reports/m5-post-delivery-review-2026-10-03.json)。

## 1. 发现与修复

| 编号 | 问题及影响 | 修复与验证 |
| --- | --- | --- |
| R1 / P2 | 离线子进程仍会继承评测令牌、OIDC 口令及真实存储/解析路径等项目配置。 | 清除全部 `DOCUCHECK_*`，仅保留测试字体；用合成配置回归，没有读出或发送真实口令。 |
| R2 / P2 | `gethostbyname` 等四个公共 DNS 入口绕过网络尝试计数。 | 五个公共解析入口统一拦截；嵌套保护、原函数恢复和严格限定的标准库 socketpair IPC 均验证。 |
| R3 / P2 | 重算 ZIP 哈希后，版本声明、源码清单或文件数可以与基线矛盾。 | 严格版本模型；重算排序后的源码清单指纹、校验文件数/安全路径及依赖哈希；7 个完整 ZIP 篡改回归。 |
| R4 / P2 | 规则升级后，真实 v1/v2/v3 结果无法读取/导出，同一会话追问在调用模型前失败。 | 按保存的规则实现准确重放并核对原 ID/结果；历史保持原版本，新 Run 从已验证事实重新计算当前 ID。只改版本标签、伪造结果和未知版本仍拒绝。 |
| R5 / P2 | 八字段抽取产生的内存事实被送进不支持内存的通用校验器，人工修正失败。 | 使用专用内存来源校验器并统一转换；修正、重绑、事实集身份和复核接通。GB/GiB/MiB 均验证提交→复核→Agent 追问，保留有原文支持的两个值及 `needs_review`。 |
| R6 / P3 | Pydantic 的布尔/整数 `Literal` 即使配置 strict，仍会按相等关系接受 0/1、浮点数或布尔值。 | 布尔声明先检查精确类型，整数声明使用严格整数及范围；10 个完整 ZIP 类型混淆用例先复现失败，再全部通过。 |
| R7 / P3 | 同一供应商、字段同时保存有效 v4/v3 结果时，旧结果会借用新版的验证状态被标为正式。 | 逐条记录已验证的当前结果 ID；读取/导出为 v3 非正式、v4 正式，新 Run 只保留一个当前结果。正常序列化本来会去重，这次用数据库合成载荷复现，未证明普通客户端可触发。 |

相关实现：[会话恢复](../backend/app/agent/session_memory.py)、[历史查询](../backend/app/db/api_queries.py)、[事实转换](../backend/app/extraction/fields.py)、[修正提交](../backend/app/db/correction_repository.py)、[修正复核](../backend/app/db/correction_recheck.py)、[归档工具](../evals/delivery_bundle.py)、[离线保护](../scripts/offline_guard.py)、[离线入口](../scripts/verify_offline.py)。

文档也已修正：开发机的 preview/build 与接收机器的 verify/restore 分开；使用不存在的新输出路径；重打历史包必须显式指定与实测版本一致的源码目录，见 [复现手册](reproduction.md)。

## 2. 测试与复核证据

| 新增测试 | 数量 | 覆盖 |
| --- | --- | --- |
| [离线保护与环境](../backend/tests/test_offline_verification.py) | 5 | 项目私有配置不继承，公共 DNS 入口在原生 I/O 前拦截。 |
| [归档验证](../backend/tests/test_delivery_bundle.py) | 17 | 版本/源码声明矛盾、布尔/数字类型混淆，拒绝时恢复目录不存在。 |
| [八字段历史会话](../backend/tests/test_session_memory.py) | 3 | 真实 v1/v2/v3 结果重验后恢复为当前结果，原保存内容不改写。 |
| [API 业务生命周期](../backend/tests/test_post_delivery_business.py) | 16 | 历史读取/导出/追问、伪造拒绝、混合版本标记、三种内存单位的修正和追问。 |
| **合计** | **41** | 首次交付 724 项增加到本次 765 项。 |

其中失败回归均先实际观察到，再修改实现；工具输出标识保存在机器汇总中。修复过程中相关六文件测试 78 项通过，最后一批归档/业务测试 55 项通过；最终判断使用下面的完整回归。

| 最终环境 | 后端结果 | pytest 输出用时 | 网络尝试 |
| --- | --- | --- | --- |
| 原 Conda，Python 3.12.14 | **765 passed / 22 skipped** | 130.92 秒 | 0 |
| 新源码目录，Windows 隔离 venv，Python 3.12.14 | **765 passed / 22 skipped** | 142.12 秒 | 0 |

原始报告：[本机完整回归](../data/m5-c-recheck/offline-reviewed-final.json)、[隔离环境完整回归](../data/m5-c-recheck/offline-clean-windows-reviewed-final.json)。报告另外记录整个 pytest 子进程用时，包含启动等开销；表中采用 pytest 自身输出。第一次候选回归的 754/22 记录也保留，没有替换为最终数字。

隔离环境使用本次复制的 341 个源码/配置文件，未复制 `.env`、业务 `data/` 或 node_modules；复用 M5-C 已实际安装的独立 venv，**本轮没有再次冷安装**。[版本读回](../data/m5-c-recheck/runtime-readback.json) 确认开发锁 97 个包全部匹配，OpenAI SDK 3.23.0、LangSmith 0.14.3、Pydantic 2.13.5；原 Conda 依赖保留。

22 个跳过均需要显式开启 MySQL、Redis、Celery、MinerU 等服务。本轮没有启动 Docker/前端、调用真实模型、重新跑 Linux 或重新测试浏览器；此前环境和业务实测以各自原报告为准。

两名单独的 AI 审查者分别只读复查业务生命周期及归档/网络边界，并对修复做限定范围的二次复核；本轮上述范围内无剩余确认问题。这是代码审查与自动回归，不是独立人员对业务资料的封存。

## 3. 源码与历史成绩分别记录

当前 76 个业务 Python 文件的指纹：

```text
8f696a670171a972e1e2af78029cee2411008ec56b1fbf5d6276ab91bbfc2b2d
```

六个业务文件有改动，具体清单和哈希见 [当前源码记录](../data/m5-c-recheck/source-as-reviewed.json)。规则版本仍为 fields-v4；本轮修复会话/修正/交付边界，没有修改采购规则版本。

M5-B 最终真实模型运行及 M5-C 首次交付对应的历史业务指纹仍为：

```text
f3938cda07b0b365ed3477b6ede3ae11fbdea4c08fe7b2483a1e85a264e9ce71
```

历史 104/104 状态、89/89 必要证据及性能结果没有重写，**本轮没有为新源码重新测真实模型质量/性能**。[原 M5-C 报告](m5-c-delivery-2026-10-03.md)、其机器汇总和最终审计保留原字节；当前工作区直接重打旧实测包会被 `source_version_mismatch` 拒绝，这是预期边界。

Git HEAD 仍为 `efcc62ab25e9e06dc4b4bba4a16d04fcfcd8c867`，分支 `phase-0-foundation`。未提交的工作区改动保留，没有自动提交、推送或发布。

## 4. 归档重验与恢复

本次归档：[docucheck-m5-development.zip](../data/m5-c-recheck/docucheck-m5-development.zip)。

```text
SHA-256: ff2a7c9001035f3f933159d878b05d5e892175165dbb06d49d7e0547d10dc2cb
白名单载荷: 227 files / 4,421,802 bytes
恢复文件: 228（包括清单），逐字节一致
```

构建使用修复前已保留的实测源码及原始报告，仅更新复现手册；这是历史开发证据包，不包含当前业务源码。旧归档 SHA `7627a71b702237eb4b9722569918505fb9f5fa3c7ee480387dc6d9bfa3b55b8d` 仍验证通过，未被覆盖。

新目录恢复后重新校验：**12 包、13 家供应商、104 格、38 原件、36 页 PDF，资产错误 0**。所有历史失败任务继续保留。记录见 [归档与恢复证据](../data/m5-c-recheck/bundle-verification.json)、[保留的实测源码证明](../data/m5-c-recheck/rechecked-bundle-source.json)。这些是字节/资料完整性检查；可信渠道的整体哈希仍需另行核对。

## 5. 验收范围与参考

本轮复查和修复已完成。正式独立资料封存仍为 0，实际账单及缓存分类未知；当前源码的真实模型复测、部署版本的外部服务集成、远程 CI、业务数据库/原件/OIDC 灾备和生产发布尚未完成，不计入本轮通过。

会话状态与持久数据的作用参考 [LangGraph 持久化文档](https://docs.langchain.com/oss/python/langgraph/persistence)；严格输入类型参考 [Pydantic strict mode](https://pydantic.dev/docs/validation/latest/concepts/strict_mode/)。本项目的版本重放、不可变历史和精确布尔约束由本地代码及回归验证，官方文档不代替这些测试。

收尾的文件哈希、链接、源码一致性与 diff/Ruff 校验见 [本轮最终审计](../data/m5-c-recheck/final-audit.json)。
