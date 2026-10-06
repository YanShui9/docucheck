# Milvus 接入基础 Implementation Plan

> **For agentic workers:** Use executing-plans for inline execution in the current checkout. 用户要求少量大步骤；当前已有大量未提交成果，保留本地工作，不为本模块复制新 worktree。

**Goal:** 为后端第二大步提供隔离部署配置、无网络预览和显式 Milvus CRUD/向量搜索基础探针。

**Architecture:** Milvus 2.6.25（用户建议拉新镜像后的已验启动版本）单容器 embedded etcd/local storage 使用独立 Linux named volume，配置随镜像构建，避免复用发生 I/O 错误的旧 Windows bind 目录。应用提供惰性 SDK 接入，默认 preview；send 仅使用唯一临时集合并在退出时清理。此探针使用合成向量，不是正式 RAG/Embedding 验收。

**Tech Stack:** Python 3.12、pytest、现有 Pydantic/SecretStr、Docker Compose、Milvus 2.6.25；pymilvus 实连前独立安装并锁定兼容版本。

## 范围与检查点

本模块属于“后端机制与动态指标”大步骤的第一个交付。其后仍需聊天/记忆、索引 worker 与混合检索、动态清单/审查子图；前端为第三大步，样式须先确认。这里不改已有 Agent、MySQL、Redis、前端或用户 .env。

### 1. 查明部署条件

- [x] 查询 Attu 安装、Milvus 旧容器及硬件，不读/输出保存的密码。
- [x] 核对退出日志：embedded etcd 写 WAL 发生 input/output error，user.yaml 同时不可读；底层存储故障原因未完全定位。
- [x] 核对官方匹配版本脚本；其 tag v2.6.7 文件仍写 v2.6.2 镜像，不能直接执行脚本冒充固定 2.6.7 部署。
- [x] 按用户建议拉取 2.6.25，在 2GiB/no-restart 限制下完成独立新栈启动 healthy；旧实例和卷保持原状。索引负载资源仍待测试。

### 2. 测试后实现配置与探针

文件：创建 backend/app/vector_store/config.py、backend/app/vector_store/probe.py、backend/app/milvus_probe.py，以及 backend/tests/test_milvus_foundation.py。

- [x] TDD：先运行新测试，确认由于目标模块缺失而失败。
- [x] 配置：URI 不允许内嵌账号、query/fragment、非 HTTP(S)；明文只允许回环或固定内部服务名；database、维度、timeout 有范围校验，token 为 SecretStr 且 repr 不显示。
- [x] 预览：不创建 SDK 客户端、不访问网络、不要求安装 pymilvus；只输出维度、数据库、是否已配置凭据及实际动作说明。
- [x] 显式 send：要求 token，惰性导入 SDK；唯一集合名，碰撞拒绝操作，不删除已有集合。实连待下方门槛，当前使用 fake 验证行为。
- [x] 合成五条向量：同范围两条，另一个项目/任务/旧快照各一条；Strong 检索及查询只能得到前两条，删除其中一条后 Strong 查询只剩一条。三种范围字段均有独立测试且实连覆盖。
- [x] 异常：连接/SDK错误只返回稳定错误码；已创建集合在 finally 清理并关闭客户端；清理失败不得返回成功。

核心测试示例（工厂 fake 在测试文件定义，完全无网络）：

    def test_preview_never_constructs_client():
        config = MilvusConfig.from_env({})
        assert preview(config)["mode"] == "preview"

    def test_probe_filters_other_case_and_snapshot():
        client = FakeClient()
        result = run_probe(configured(), client_factory=lambda config: client)
        assert result["matched_ids"] == [1, 2]
        assert result["remaining_ids"] == [1]
        assert client.closed and client.dropped

    def test_existing_collection_is_not_deleted():
        client = FakeClient(existing=True)
        with pytest.raises(MilvusProbeError, match="probe_name_collision"):
            run_probe(configured(), client_factory=lambda config: client)
        assert not client.dropped and client.closed

运行：Conda docucheck Python 执行 pytest -p scripts.offline_guard backend/tests/test_milvus_foundation.py -q。先 FAIL，实施后 PASS 且网络尝试为零；再运行 Ruff 及既有 Provider/检索定向回归。

### 3. 准备隔离 Docker 配置

文件：compose.milvus.yaml、deploy/milvus/Dockerfile、deploy/milvus/embedded-etcd.yaml、deploy/milvus/user.yaml、.env.example。

- [x] 部署文件固定 Milvus 基础镜像 2.6.25 及 digest；配置构建入镜像，数据用独立 named volume；构建上下文仅 deploy/milvus，不包含 .env。
- [x] 部署文件只向宿主回环映射 19530 和健康端口；etcd 不暴露宿主端口。
- [x] 配置启用 Milvus 认证；默认管理员密码未轮换前不标业务就绪。凭据创建/轮换仍待实连。
- [x] Docker Compose config --quiet 校验；初始暂停启动，后按用户建议补充受限试启动并通过。

### 4. 实连门槛与报告

- [x] 受限 2GiB 栈小规模验收可运行；安装 SDK 2.6.17，更新环境及 dev/Windows/vector 锁，pip check 通过。完整 Embedding/索引资源仍待测。
- [x] 新栈启动/健康，匿名请求被拒绝；2GiB 受限试运行，没有 OOM/重启。
- [x] 管理员凭据轮换、专用库/账号权限；收紧 public 三项默认全局授权。重复初始化、匿名/旧默认凭据拒绝、跨库结构/内容及账号管理拒绝已实测。
- [ ] Attu GUI 连接；已提供本机连接说明，未操作 GUI。
- [x] python.exe -m backend.app.milvus_probe --send 成功，确认临时集合清理，无业务集合变更。
- [x] 记录服务端 2.6.25、SDK 2.6.17、Attu 2.6.4、观测内存及结果；此结果不冒充真实 BGE-M3 召回、索引 worker 或业务 ACL 验收，未测完整负载峰值。

本模块 offline 准备和 live 验收分别报告。缺资源/SDK时完成独立可做的代码及验证，保留实连未完成状态，不擅自停止旧服务或删除数据。

当前离线结果：新模块 31 项，连同既有 Provider/检索定向回归 81 passed，网络尝试 0；Ruff 和 Compose 配置校验通过。详见 [本步报告](../../milvus-foundation-2026-10-03.md)。

后续实连更新：基础探针 32 项 + 本机初始化 12 项离线回归通过；完整后端 809 passed、25 skipped，网络尝试 0；显式 Milvus 实连 3 passed。详细变更、失败后补强和剩余范围见 [实连报告](../../milvus-live-acceptance-2026-10-03.md)。
