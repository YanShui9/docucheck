# 实连验收第二部分 C：模型连接的 TLS 根因与受控修复

日期：2026-10-08。源码基准：`365c832`。本步仅处理真实模型连接异常，不开始前端改版或向量库恢复。

## 根因证据

上步新合成苹果任务 `832da6f4a512476c86a2cfc79a7fe30e` 的完整审查和一次显式重试均为 `partial / connection_error / outcome_unknown`。本步在同一worker加载该合成快照、清单和会话历史，执行一次诊断决策，不写入正式Run结果。

诊断在0.107秒内失败，安全异常链为：ProviderError → OpenAIConnectionError → APIConnectionError → httpx2.ConnectError → httpcore2.ConnectError → ssl.SSLCertVerificationError。OpenSSL验证码19，证书链中存在未受容器信任的自签名根。不是模型已经返回业务结果后解析失败。

不携带密钥的OpenSSL握手（启用 `-verify_return_error`）显示：`api.deepseek.com` 的证书由本机 **Kaspersky Anti-Virus Personal Root Certificate / AO Kaspersky Lab** 签发。根证书SHA-256：

```text
271F539FA9F9DDA9702F457689E4535CC0102639F2F5C8B0DA918C55609BF3A0
```

该指纹精确匹配Windows `LocalMachine/Root` 和 `CurrentUser/Root` 中已信任的卡巴斯基根证书，2033-11-11到期，导出对象无私钥。容器已有150张系统CA，但没有这张本机根证书。诊断未输出API key、Authorization、完整请求/响应或用户私有资料。

本机SDK版本：openai3.23.0、langchain-openai1.6.6、httpx2/httpcore2 2.13.1，httpx0.28.1。HTTPX2默认系统信任库与旧HTTPX的certifi默认不同，但**本轮HTTPX与HTTPX2都复现证书错误**；上步一次GET401不能证明传输稳定，也不能把“切换HTTPX2”单独认定为根因。直接根因是被检查的HTTPS证书链缺少容器信任锚。

参考官方[OpenAI SDK HTTPX2迁移及TLS说明](https://github.com/openai/openai-python/blob/main/httpx2.md)、[HTTPX2迁移说明](https://httpx2.pydantic.dev/migration/)。这些文档支持使用受信任系统CA或明确的 `SSL_CERT_FILE`，不支持用关闭验证代替修复。

## 源码与可选运行配置

- Provider只检查有限深度、带循环保护的异常类型链，对证书验证错误记录安全detail `tls_certificate_verification`，不复制错误消息、证书内容、请求头或响应体。`connection_error`、结果未知和禁止自动重试的协议保持不变。
- Assistant把已有白名单detail写入模型事件，使之后的任务可从持久化事件定位证书类故障。
- `compose.model-tls.yaml` 为显式选用的worker配置：使用绝对宿主PEM路径，只读挂载，`create_host_path: false`，缺少路径时Compose拒绝启动。保留原公共CA，继续校验证书与主机名。
- 原有 `compose.yaml` 和 `compose.vector-app.yaml` 不改。配置比较验证其他服务完全相同、worker原网络和data挂载保留、仅增加证书环境变量和只读挂载。证书不烘焙进镜像，不提交Git，不加入`.env.example`的启用默认值。

本机候选bundle已从**现有worker公共CA＋Windows指纹匹配的根证书**生成，路径 `.local-trust/model-ca-bundle.pem`，生成时SHA-256 `DC611EA7E32E13CC5F9A3B27AABC0837F2C4A2A86BB04633E10D8BF1869CE636`。这是当前机器的准备产物，不供其他开发者盲目复用。证书轮换后须重新核对本机可信来源、指纹与有效期。不能信任从失败连接临时下载、但未匹配已核准本机根的证书。

独立复核发现最初的 `data/local-trust/` 在现有可写共享data挂载内，另一个路径仍可能改写同一文件。已将候选bundle移至独立的忽略目录 `.local-trust/`，既有worker/indexer/cleaner的data挂载均无法访问它；只通过新增只读挂载暴露给worker。不得把信任文件放回共享data目录或用户上传目录。

## 应用与回滚

**应用证书前等待用户明确确认。** 原因是加入额外信任锚会使worker信任由该根签发的HTTPS证书，扩大其证书信任范围；源码和配置准备不等于已获授权应用。无需关闭卡巴斯基或TLS验证，不改Windows信任库。

确认后先检查该bundle与已核对指纹/哈希一致，进行不生成文本的SDK TLS探针；再在没有活跃worker任务时，使用既有向量overlay一起构建并重建worker。以下命令只作为执行说明，本文写入时尚未应用：

```powershell
$env:DOCUCHECK_MODEL_CA_BUNDLE = 'E:/PycharmProjects/docucheck/.local-trust/model-ca-bundle.pem'
docker compose -f compose.yaml -f compose.vector-app.yaml -f compose.model-tls.yaml --profile agent up -d --no-deps --build worker
Remove-Item Env:DOCUCHECK_MODEL_CA_BUNDLE
```

长期开发启动须保留相同三个Compose文件，并在本机`.env`设置已核准的绝对路径。证书只读挂载属于worker的运行配置，不由网页用户或模型提供。

回滚时省略TLS overlay重建worker，恢复原环境和卷配置，不删除数据库或原件：

```powershell
docker compose -f compose.yaml -f compose.vector-app.yaml --profile agent up -d --no-deps worker
```

回滚后仍会受到原证书链问题影响，不能把回滚可用解释为模型网络可用。重建前均检查运行任务，保留waiting_user，不强制终止实际正在执行的模型请求。

## 本步验证与尚未完成

- 修复前，三个新增回归复现安全TLS分类/Assistant事件缺失；修复后Provider、Assistant、探针、Graph和Dynamic Agent定向 **138 passed / 网络0**。包括SDK原生HTTPX2 mock异常链、无重试、秘密不进traceback、伪造错误文本不能声称TLS故障、循环异常链终止。
- Ruff（backend/evals/scripts全量）、pip check通过。最终完整后端回归 **1266 passed / 41 skipped / 网络尝试0**，268.42秒；41项外部服务测试不算通过，前端本轮未修改或重复测试。
- Compose合并对比及缺少bundle拒绝检查通过；没有调用模型验证配置解析。
- 本步诊断请求1次失败，用量未知；前两次辅助脚本因import路径错误在请求前退出，调用数0，随后使用实际容器工作目录修正。不能把该诊断视为业务审查结果。
- 证书应用、worker重建、修复后模型探针与本轮新资料完整审查仍待用户确认后执行。前端未修改；Milvus/Ollama实连仍为下一项。
- 补充宿主GET探针同样出现验证码19；当前工具进程显式设置了 `SSL_CERT_FILE`，不会自动采用Windows根库。没有修改该宿主环境或宣称宿主模型已可用，本步应用方案作用域仍仅worker。

原始安全诊断及配置检查在被忽略的 `data/verification/model-connection-2026-10-08/`；[脱敏机器证据](repair-model-tls-evidence-2026-10-08.json)绑定本次源码、诊断和配置检查哈希。公共证书bundle在被忽略的独立 `.local-trust/`，不提交其内容。提交号以实际Git日志和交付回复为准。
