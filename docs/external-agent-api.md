# 外部 Agent API：首批服务端实现

> 2026-09-22：原生读取、手工保存与生成接口边界已调整，见[原生编辑器与页面版本](native-editor-coordination-2026-09-22.md)。这是源码与隔离验证状态，运行中的 Docker 尚未更新；下文部署记录为对应日期的历史结果。

> 2026-09-21 后续修订：按用户确认取消 Presenton 写租约，以[无租约实现与验收](mcp-no-lease-2026-09-21.md)为准。本修订是原 Issue 设计基线下的细化；本文先前的 acquire/renew/release、writerEpoch、客户端接管和租约验收条目由新方案替代。保留文档归属、当前 UI、版本校验、事务、请求去重及固定修订导出。历史验收记录不代表新版本验收结果。

2026-09-21 更新：外部 Agent 模式默认 `/mcp` 已改为业务目录并部署到本机 5002，`/mcp/workflow` 保留为别名，原有 11 个 `agent_*` 工具移至 `/mcp/legacy` 兼容地址。核心实现见[业务 MCP 实现记录](mcp-workflow-implementation-2026-09-21.md)，运行证据见[本地部署验收](mcp-workflow-deploy-2026-09-21.md)。下述首批边界及历史部署记录是旧目录的交付范围。普通客户端刷新工具目录；需要旧协议的客户端显式使用兼容地址。

本实现是既定方案的阶段交付：先建立 P1/P2 的通用身份、持久稿件、受控原生写入链路，并实现部分模板与已有素材绑定能力。方案中的完整编辑器交接、生成任务、固定修订导出、对象存储和客户端端到端验收继续有效，不能用本阶段测试替代。

## 1. 已实现范围

- 外部客户端使用 Presenton 原生账号/API key 鉴权。HTTP 与现有 FastMCP 共享同一 API、原生工具 Schema 和数据。
- 文稿继续使用 `presentations`、`slides` 与 `v2-standard`，增加协调记录、独立调用会话及操作回执。没有新增内容生成引擎或生产依赖。
- 原生 `ChatTools` 新增一次性严格执行入口；`PresentationChatMemoryLayer` 新增显式执行策略。默认原生行为保持，受控执行使用外层事务，禁止自动图片生成、MEM0 和执行后参数修复重试。
- 提纲确认后才能选择模板。选择时复制 V2 layouts 和 theme，保存与填充复用原生逻辑。原有模板行随后发生变化，不影响已经复制的布局。
- 当前持久化 `ui` 与 `speaker_note` 为读取依据。搜索、页面详情与投影使用同一修订。文本优先级与原生画布一致，页面替换保持 slideId；替换时未提供备注则保留已有备注。
- 原生图片上传继续可用。首建、替换布局、局部元素/组件写入均校验已有素材，不下载任意 URL、不自动补图、不替换为占位图。素材必须为当前 owner 可解析的原生 ImageAsset，或所选模板使用的本地静态素材。
- 字数限制作为布局提示返回；页面内容结构、列表数量、字段类型、ID 等约束仍有效。通过 Schema 校验不表示真实渲染已经没有溢出。

## 2. 启用方式与隔离

API 和 MCP 进程都设置 `PRESENTON_EXTERNAL_AGENT=true`，并启用原生认证。`DISABLE_AUTH=true` 与该模式不兼容，服务会拒绝启动/请求。

先备份并通过独立迁移任务升级到 `d9f136b5c842`，然后启动新版本；原有库不能靠 `create_all` 补列。历史合并节点 `c8e025a4b731` 保留已部署的旧任务迁移 `17a9c2e4f6b8` / `29b8d3a6c1e0`，与文稿协调迁移 `b7d914f3a620` 汇合，新 head 追加业务引用表及生成 gate。多实例不要同时运行启动迁移。沿用已有 `DATABASE_URL`、数据目录及原生认证配置，不需要配置 Presenton 模型凭据。

此模式关闭原生模型检查、用户模型配置更新、cloud proxy 和明确的内部生成能力。原生读取、模板/主题查询及手工编辑接口保留；受管稿件保存必须经过页面版本与事务检查。MCP 只注册本模式的工具和 instructions。

### 记忆职责与模式优先级

外部 Agent 负责对话上下文、推理和长期记忆。Presenton 保存的是原生稿件、页面 UI、备注、修订、操作回执和后续导出所需数据；这些业务持久化不依赖 Mem0，也不能用向量记忆代替。

`PRESENTON_EXTERNAL_AGENT=true` 优先于 `MEM0_ENABLED`：即使后者未设置或误设为 `true`，公共记忆入口也直接返回禁用结果，不初始化 Mem0、Qdrant 记忆库、spaCy 或 FastEmbed，不访问旧的共享记忆客户端。Presenton 原生的 `PresentationChatMemoryLayer` 名称保留，它仍提供确定性的稿件读写；复用这个类不表示启用模型记忆。

Node 启动器在读取配置、启动子进程前关闭 Mem0、Ollama、内部图片生成和模型配置入口；直接启动 Python 也受公共 Mem0 入口及 API 能力检查约束。外部模式镜像构建跳过 spaCy 语言模型安装和 FastEmbed 模型预热。普通原生模式仍可显式使用其既有可选记忆功能，无须删除上游源码、历史记忆目录或稿件数据库。

### Docker 部署必须与 MCP 注册匹配

现有四种 Compose 服务均传入 `PRESENTON_EXTERNAL_AGENT`。外部模式建议使用专用覆盖文件，同时设置镜像构建参数和容器运行环境：

```sh
docker compose -f docker-compose.yml -f docker-compose.external-agent.yml up -d --build production
```

这是标准 Compose 部署示例。升级已有容器前必须核对原镜像、端口、数据挂载和认证配置，备份后保留原数据；不能把示例的 `./app_data` 当作现有实例的数据目录，也不能仅设置宿主机环境变量而不检查容器实际配置。外部模式要求认证，`DISABLE_AUTH=true` 会使启动失败；应使用目标实例签发的 API Key，不能复用另一套演示数据库的凭据。

更换 MCP URL、看到 `/mcp` 返回响应或 `Connected`，都不表示镜像已经升级。认证后应确认：

- 默认 `/mcp` 工具目录为 7 个 `ppt_*`，包含合并初始化的 `ppt_start`；不再暴露 session/lease 操作，也不存在 `start_standard_presentation`、`start_smart_presentation` 等原生生成工具。
- 明确检查兼容目录 `/mcp/legacy` 时，目录仍为 11 个 `agent_*`；其中 `agent_capabilities` 的 `internalAgent`、`internalMemory`、`internalImageGeneration` 都为 `false`。
- 直接请求原生 `/api/v1/ppt/presentation/generate/async` 等生成入口得到模式禁用错误；不能仅从目录中隐藏它们。
- 冷缓存首次建稿、保存和读取不产生 Mem0/FastEmbed 模型下载、内部 Agent 或模型调用；保存后 UI、备注和修订可正常读取。

### 2026-09-20 本地部署差异记录

排查发现本机 Docker `5002/mcp` 使用的镜像仍暴露原生生成工具，`PRESENTON_EXTERNAL_AGENT` 未设置，Mem0 默认开启。原生异步生成被接受后，为保存生成上下文触发 BGE 向量模型下载；日志中的 `Fetching 5 files` 是模型下载进度，接口 `200 OK` 只代表后台任务已接受。此前修改 Claude Code 的 MCP 地址没有改变镜像内容。

独立本机演示服务与 Docker 使用不同数据库和凭据；演示 MCP `8911` 已按用户要求关闭。上述问题在只更新本地代码时仍然存在；后续按用户授权完成了下述 `5002` 部署切换。

代码补齐阶段验证：隔离 Linux 容器中 29 项记忆/工具/HTTP-MCP 回归全部通过，其中 7 项覆盖外部模式记忆及模型预热保护。Windows 同组测试为 28 项通过、1 项因既有 POSIX 路径断言失败，该项在 Linux 复核通过。启动器执行检查确认，即使传入相反的模型设置，也会在启动服务前关闭它们；生产/开发 Dockerfile 的 `docker build --check` 均无警告，基础与外部覆盖 Compose 的四种服务配置均检查通过。该阶段尚未替换容器；后续部署结果见下节，冷缓存全流程生成仍待验收。

### 2026-09-20 后续 Docker 部署与回滚记录

已将 `presenton-truth-agent` 的 `127.0.0.1:5002` 切换到 `presenton-truth-agent:external-agent-20260920-175832-r2`，镜像 ID 为 `sha256:728aa9b1790cffa41518526c6ea2b0efab1375e3bbc0d9b91b18f93fef3e3738`。本次使用固定旧镜像的运行依赖，完整替换服务端源码并更新启动器；依赖锁文件与旧镜像相同。这是本地增量镜像部署，不等同于从标准多阶段 Dockerfile 全量重建。

部署先在原数据库副本上验证。发现并补齐旧任务迁移历史与合并节点后，17 项迁移回归通过；副本经真实 HTTP MCP 完成建稿、写提纲、读取修订和 30 分钟租约验证。正式切换前确认无活动任务，停止旧容器并备份全部数据，原挂载路径保持不变。迁移后仍有原来的 3 份稿件和 7 页幻灯片；六张稿件/旧任务表的内容指纹一致。原生账号归属回填更新了 owner 和稿件更新时间，没有修改稿件内容。

原实例未配置账号、关闭认证。现已启用原生认证，创建本机账号并签发 90 天 API Key，Claude Code 用户级 `presenton` 配置已同步为 `http://127.0.0.1:5002/mcp`，`claude mcp list` 显示 Connected。登录信息及 API Key 只保存在本地受限权限目录和客户端配置中，文档不记录凭据。

运行实例已核验：未认证工具请求返回 401；认证后的原生 Standard/Smart/Chat 入口返回 409；MCP 仅有 9 个 `agent_*` 工具，内部 Agent、记忆和图片生成能力均为 false；误设 `MEM0_ENABLED=true` 时公共记忆入口仍返回禁用结果。此次启动与验证期间未出现 DashScope 内部生成、Mem0 写入或模型下载日志。用户需要重载原 Claude 会话，以刷新认证和工具目录。

回滚容器 `presenton-before-external-20260920-175832` 已停止保留。受限备份目录为 `tmp/external-agent-deploy-20260920-175832/cutover-backup`，同级保留镜像源码清单、验证报告和私密登录文件；这些本地文件不提交。回滚必须同时恢复旧镜像和对应数据备份，不能把旧镜像直接接到升级后的数据库，也不能用强制修改迁移版本代替恢复。

本次只验收单机 Docker 切换、工具访问及隔离副本的实际写读；未完成真实 Pi/Claude Code/Codex 三端接力、浏览器交接、持久任务、固定修订导出和冷缓存全流程制稿。

### MCP 执行耗时日志

MCP 统一中间件为每次请求记录开始和结束事件，覆盖外部 Agent 与原生模式中的全部工具，也覆盖 `tools/list` 等协议请求。启动器将 MCP 进程的 stdout/stderr 接入 Docker 日志；默认 INFO 级别即可查看，无须修改客户端注册。

```text
INFO: [mcp.call.start] call_id=... method=tools/call tool=agent_read_document
INFO: [mcp.call.finish] call_id=... method=tools/call tool=agent_read_document status=success duration_ms=42.37 error_type=-
```

示例耗时仅说明格式。相同 `call_id` 对应一次调用；并发调用分别计时。`status` 为 `success`、`error` 或 `cancelled`；异常和 MCP 错误结果同样记录耗时，异常只记录类型，不记录异常正文。日志不包含工具参数、返回正文、认证头或会话令牌。工具成功返回业务 `rejected` 回执时，仍需由调用者读取回执判断文稿是否保存，不能把 MCP 执行成功等同于业务成功。

`duration_ms` 使用单调高精度时钟，范围是服务端 MCP 中间件接收到请求后，到工具及其内部 HTTP 调用返回、抛错或取消为止；不包含外部 Agent 推理、客户端网络等待、响应传输，也不代表异步后台任务的总完成时间。鉴权前被 HTTP 层拒绝的请求只产生访问日志；进程被强制终止时可能只有开始事件。

查看当前实例：`docker logs -f presenton-truth-agent`。PowerShell 可用 `docker logs -f presenton-truth-agent 2>&1 | Select-String '\[mcp.call\.'` 只看计时事件。

2026-09-21 源码验证：7 项新增测试覆盖准确计时、成功、异常、取消、错误结果、日志单行保护以及两种模式中的并发调用与工具目录请求；连同认证及真实 HTTP/MCP API 回归，共 32 项通过。启动器通过 `node --check`。

同日本机 `5002/mcp` 已切换到增量镜像 `presenton-truth-agent:mcp-timing-20260921-094718`，仅更新 MCP 入口、计时中间件和启动器，保留依赖、端口、认证及数据挂载。通过客户端现有注册实际调用，Docker 日志中两次并发 `agent_capabilities` 分别记录 65.01 ms 和 67.69 ms；无效调用会话导致的 `agent_read_document` 错误记录 37.20 ms、`status=error`、`error_type=ToolError`。这些数值是单次检查结果，不是性能基准。

切换前确认无活动生成任务或写租约，备份数据库并保留上一容器 `presenton-before-mcp-timing-20260921-094718`。当前 5 份稿件和 13 页幻灯片保留；工具目录仍为 9 项，内部 Agent 和 Mem0 能力仍关闭。备份、三个运行文件的源码哈希和脱敏计时证据位于受限本地目录 `tmp/external-agent-deploy-20260920-175832/mcp-timing-20260921-094718`，不提交凭据或运行数据。

### 稿件保护与当前限制

受控文稿带 `agent_managed=true`。原生查询按用户归属可见，两个原生保存接口已接入协调保存；未适配的批量更新/删除和 ORM 写入仍不能绕过版本保护。部署本轮前后端后才能使用该编辑能力，完整原生画布与真实 Agent 接力验收仍需单独完成，不能用历史 Docker 结果替代。

SQLite 可用于本地单请求功能测试；生产协调目标是 PostgreSQL。素材当前通过原生文件解析器读取，多个实例需要解析同一数据目录。共享对象存储、固定字体依赖和真实跨节点恢复尚未实现。

## 3. HTTP 与 MCP 接口

所有路径以 `/api/v1/agent-tools` 开头。除 capabilities 和 sessions 创建外，均要求原生身份及 `X-Presenton-Session` 请求头。调用会话不能代替原生身份认证。

| HTTP | MCP 工具 | 用途 |
| --- | --- | --- |
| `GET /capabilities` | `agent_capabilities` | 协议、能力状态、instructions、原生工具 Schema |
| `POST /sessions` | `agent_open_session` | 为一个 Agent/标签页创建独立调用会话 |
| `POST /documents` | `agent_create_document` | 使用预先生成的 operationId 幂等建稿 |
| `GET /documents/{document_id}` | `agent_read_document` | 读取当前 UI、备注、提纲、revision、阶段与写权状态 |
| `GET /operations/{operation_id}` | `agent_read_operation` | 查询不确定请求的持久化回执 |
| `POST /documents/{document_id}/lease` | `agent_writer_lease` | 获取、续期、释放本会话写租约 |
| `POST /documents/{document_id}/read-tool` | `agent_read_tool` | 在一致修订上执行原生只读工具 |
| `POST /documents/{document_id}/mutations` | `agent_mutate_document` | 执行一项原生写工具或生命周期动作 |
| `POST /documents/{document_id}/read-tools` | `agent_read_tools` | 在同一修订上读取 1–20 项原生只读工具 |
| `POST /documents/{document_id}/mutation-batches` | `agent_mutate_document_batch` | 原子执行 1–20 项原生写工具/生命周期动作 |
| `GET /templates` | `agent_list_templates` | 列出本用户可访问且已编译的 V2 模板 |

FastMCP 直接从该路由生成 OpenAPI 工具；其参数保留 `X-Presenton-Session` 字段，调用时填入当前客户端自己的 sessionToken。无需为 Pi、Claude Code、Codex 分别实现服务端业务。

### 会话与创建

`POST /sessions` 的请求为 `{"label":"my-client"}`，返回服务端生成的 sessionId、sessionToken 与 expiresAt。token 为随机凭据，只存储哈希；客户端应保密，不能把一个会话共享给多个 Agent 或浏览器标签页。当前有效期为 24 小时。

在发出建稿请求前生成 UUID operationId，重试保持同一内容：

```json
{
  "operationId": "11111111-1111-4111-8111-111111111111",
  "title": "项目汇报",
  "language": "Chinese"
}
```

建稿初始阶段为 `outline`、revision 为 0。每次实际业务修改增加一次 revision；租约操作和真正的 noop 不增加内容修订。operationId 在同一 owner 内唯一，跨文稿或不同 payload 复用会返回 `operation_id_reused`。

### 写租约

`POST /documents/{id}/lease`：

```json
{"action":"acquire"}
```

返回 writerEpoch、expiresAt 和当前 revision。租约为 30 分钟（1800 秒），绑定可信调用会话，且不超过该会话的到期时间。续期与释放必须带原 epoch：

```json
{"action":"renew","writerEpoch":1}
```

只有仍有效的同一会话租约可以续期或释放。同一用户的其他 Agent/标签页不能共用写权；租约过期后重新 acquire 增加 epoch。长推理后应先查询不确定操作的回执，再重新取权、读取当前稿件并制定修改。迟到的 renew/release 不能影响新写者。

### 原生工具写信封

```json
{
  "operationId": "22222222-2222-4222-8222-222222222222",
  "expectedRevision": 0,
  "writerEpoch": 1,
  "tool": "addOutline",
  "arguments": {"content":"# 项目概况\n目标与当前进展", "index":null}
}
```

当前开放：`addOutline`、`updateOutline`、`deleteOutline`、`saveSlide`、`addNewSlide`、`deleteSlide`、`addElement`、`updateElement`、`deleteElement`、`addComponent`（原生别名 `createComponent` 同样可用）、`updateComponent`、`deleteComponent`。

只读工具：`getSlideAtIndex`、`searchSlide`、`getAvailableLayouts`、`getContentSchemaFromLayoutId`、`getAvailableBlocks`。实际参数以 capabilities 中的原生 Schema 为准。页码严格从 0 开始，越界不回退到前一页，也不静默夹到最后一页。

工具调用、修订号及回执在同一事务提交。原生业务返回 `saved:false` 等标志时，整个工具改动回滚并记录 rejected 回执；不能仅以调用未抛错判断保存成功。未知异常导致整次事务回滚，不做盲目重试。

| 结果 | 含义 |
| --- | --- |
| `status: applied` | 业务改动及回执已一起提交 |
| `status: noop` | 内容未变化，revision 不增加 |
| `status: rejected` | 未保存，handler 的改动已回滚 |
| `replayed: true` | 返回同 operationId 的原始回执；其中 revision 是该次操作的修订 |
| HTTP 409 `revision_conflict` | 重新读取稿件，再规划修改 |
| HTTP 409 `writer_lease_lost` | 先查回执，重新取权并读取，不复活旧 epoch |
| HTTP 409 `writer_busy` | 其他调用会话仍持有有效写权 |

### 提纲、模板与完成

阶段为 `outline → outline_confirmed → composing → ready`。三个生命周期动作同样使用上述写信封：

1. `confirmOutline`，arguments 为 `{"confirmed":true}`。外部客户端取得用户确认后提交；非空提纲才可确认。
2. `selectTemplate`，arguments 为 `{"templateId":"实际可访问的模板 ID"}`。复制布局后读取 layoutId 与内容 Schema，再用原生 `saveSlide` 提交页面。内容仍采用原生 JSON 字符串格式。
3. `completeDocument`，arguments 为 `{}`。必须已保存与提纲数量一致的非空白页面。

`composing` 阶段不允许通过空白页插入或删页跳过提纲页数检查。进入 `ready` 后可继续受控编辑、增删页面。这里尚无独立 generationId、固定槽位、执行租约恢复或取消状态机；`completeDocument` 也不表示已渲染、导出或交付。

素材字段扩展原生 Schema：图片需要 `image_url`，图标需要 `icon_url`；相应 prompt/query 可以保留，但不会执行。源自原生上传 API 的 URL 在首建、替换、局部编辑中均受同一 owner/文件解析检查。远程任意 URL、缺失素材和无凭据 blob/data 引用不在本阶段支持范围内。

原生默认模板导入器将内置素材复制到 `/app_data/templates/{templateId}/static/`。这类素材仅在当前模板快照已引用、路径合法且文件存在时允许保留；并非开放全部模板目录或任意远程 URL。真实客户端样例发现并补齐了此前仅识别 `/static/` 的遗漏，新增 General 模板真实保存及非选中素材拒绝回归用例。

### 批量调用扩展（2026-09-21）

新增批量只读和批量写工具，当前目录为 11 项；原有 9 项工具及单项写入语义保留。capabilities 增加 `batchReadTools: true` 和 `atomicBatchMutations: true`。批量调用继续复用 ChatTools 校验与 handler，不运行内置 Agent，也不引入模型循环或生产依赖。

读批量请求为 `{"operations":[{"tool":"getContentSchemaFromLayoutId","arguments":{"layoutId":"实际ID"}}]}`，保持同一文稿锁和修订，按顺序返回 `results`。仅查询实际采用的版式，不能用批量接口无界抓取所有内容。

写批量请求包含 `operationId`、`expectedRevision`、`writerEpoch` 和 `operations`（每项仅 `tool`、`arguments`）。整批使用一个事务、一个操作回执：有净变化时 revision 加 1，完全无变化为 noop；任意项拒绝时所有已执行项回滚，记录 rejected 回执及从 0 起算的 `result.failedIndex`。异常、租约失效、回执失败均不得部分提交。参数修正后使用新 operationId；不确定响应重试保持原始请求，已提交回执可在释放租约后读取，不能重复写入。

提纲与用户确认可以在一批提交，但调用方必须先取得用户确认；选择模板仍在确认之后。6 页内容和 `completeDocument` 可以合为第二批，完成动作仍校验所有提纲页已经保存。每批最多 20 项，较大稿件按批持久保存；不同批之间保留既有修订与租约边界。UI、备注、权限、素材验证与原生单项工具相同。

验证：SQLite 新增 8 项通过（另 2 项仅适用 PostgreSQL）；包含原有工具/API/计时的先行回归 22 项通过。临时 PostgreSQL 上批量、API 与并发回归 17 项通过，其中包括重复批量幂等和同修订竞争。批量 HTTP/MCP 的真实序列化路径已验证。计数有交集，不能相加为互不重叠的总测试数。

本机 Docker 已升级为 `presenton-truth-agent:agent-batches-20260921-104344`，只替换 API 与工具服务的四个源码文件，无数据库迁移。保留原端口、配置与数据挂载，备份数据库并保留前一容器；升级前已有 6 份稿件、19 页，切换检查数量不变。客户端重连后应能看到 11 项工具及批量能力。真实模型制稿时延另行记录，不用工具测试耗时替代。

最终 SQLite 工具/批量/API/计时回归为 30 项通过、2 项 PostgreSQL 专用用例跳过；这两项已在上面的 PostgreSQL 17 项中执行通过。运行容器的四个文件哈希与部署清单一致；逐行核对升级前已有的文稿、页面、文稿状态、操作回执，均保持不变。后续性能样例各自创建新的独立文稿。

真实 Claude Code 2.1.206、Qwen3.7 Plus、xhigh：从传入用户已经确认的《大语言模型技术简介》6 页提纲，到数据库保存 6 页带讲稿、revision 3、ready，用时 203.773 秒；客户端最后答复完成为 220.822 秒。约第 42 秒批量保存提纲并确认，第 92 秒结束所选 Schema 查询，第 168 秒首次页面批量因第 0 页数组长度不符而拒绝，第 204 秒修正后的整批提交成功。释放租约后再次查库验证状态与页数。

相对于原记录中 522 秒的保存阶段，单次样例缩短约 61%。模型和推理档位相同，但此处同时采用批量优先提示词、已确认提纲和仅 Presenton MCP/Read 的工具集合；不是仅改变一个变量的严格 A/B，更不能代表冷缓存 P95 或所有模板质量。没有执行导出/视觉复核，也没有修改用户的默认模型配置。原始客户端记录含调用令牌，保存在受限忽略目录 `tmp/external-agent-deploy-20260920-175832/agent-batches-20260921-104344/claude-benchmark`，文档只摘录脱敏统计。

同输入 medium 对照未完成：客户端从约第 115 秒起将外层 `operations` 反复序列化成字符串，HTTP 422 后持续重试，超过四分钟仍未保存页面。停止这一测试并释放其写租约，保留独立测试稿；没有把失败测试当作提速证据，也未改变默认档位。

针对这份真实请求增加窄范围兼容：批量请求允许对 JSON 编码的操作数组解码一次，再执行原本的 1–20 项、对象字段及原生工具校验；JSON 对象、null、空数组、无效 JSON、超量数组仍拒绝。推荐客户端直接传数组，仅 `saveSlide.arguments.content` 需要 JSON 字符串。规范数组和等价字符串形成同一操作摘要与回执，不重复提交。该兼容及批量/API 回归 16 项通过，2 项并发专用用例由前述 PostgreSQL 测试覆盖。

兼容修复已部署至 `presenton-truth-agent:agent-batches-20260921-105838`，保持同一端口、配置和数据，保留前一容器与数据库备份。真实 HTTP MCP 已用 JSON 编码数组读取既有样例修订 3，验证容器实际采用新校验逻辑；未重新进行完整 medium 生成测试，因此不提供其完成时间。xhigh 的 203.773 秒来自前一批量镜像，属于明确记录的那次实际测量。

## 4. 验证与验收状态

测试文件：`tests/unit/test_agent_tools.py`、`test_agent_tools_api.py`、`test_agent_tools_postgres.py`；保留并运行原有编辑、素材、认证、模板、MCP 和迁移测试。`test-all.yml` 增加临时 PostgreSQL 服务和专门的事务验证步骤。

后端目录安装现有锁定依赖：`uv sync --locked --dev`。本地设置独立测试数据目录与 SQLite DATABASE_URL 后运行：

```text
uv run --locked python -m pytest tests/unit/test_agent_tools.py tests/unit/test_agent_tools_api.py -q
```

设置 `PRESENTON_TEST_POSTGRES_URL` 为可丢弃 PostgreSQL 数据库的 `postgresql+asyncpg://...` 地址，再运行：

```text
uv run --locked python -m pytest tests/unit/test_agent_tools.py tests/unit/test_agent_tools_api.py tests/unit/test_agent_tools_postgres.py -q
```

PostgreSQL 测试为每个用例创建随机独立 schema，退出时清理；未设置专用变量时，并发/迁移专项明确跳过，绝不自动使用业务 DATABASE_URL。Windows 如遇系统临时目录权限问题，可将 pytest `--basetemp` 指向仓库 `.pytest_cache` 下新建的专用目录。

本批完成项与完整验收分开记录：

2026-09-20 本地验证：Windows、Python 3.11.15 下 183 项相关回归通过；临时 PostgreSQL 16 数据库上 19 项工具/API/并发/迁移测试通过，其中包含重复在 PostgreSQL 上执行的工具用例，不应将两组数量视为互不重叠的用例总数。测试库已清理。POSIX 文件权限断言保留在 Linux 执行，Windows 文件 ACL 未作部署验收。

- [x] 原生 Schema/handler 复用，标准编辑路径回归。
- [x] HTTP/MCP 在同一真实数据库中保存并读取同一稿件，独立调用会话可区分。
- [x] SQLite 与 PostgreSQL 验证原生业务拒绝、异常、回执失败均无部分保存。
- [x] PostgreSQL 并发建稿、重复操作、同修订冲突、读写修订一致性与写租约互斥。
- [x] 首建、替换、局部图片写入保留已有素材；缺素材/任意 URL 被拒绝；替换保留页面 ID 与未显式修改的备注。
- [x] 提纲确认/选模板顺序、无内部模型调用的受控工具路径、禁用旧模型 API 和启动检查。
- [x] 外部模式优先于 Mem0 开关；公共初始化及缓存客户端不可使用，模型预热跳过；覆盖误设开启、未设置和原生兼容路径。
- [x] 四种 Compose 服务的合并配置检查；外部覆盖文件同时设置构建模式和运行模式。
- [x] 本机 Docker 实例升级后，实际验证认证、9 个外部 MCP 工具、原生生成 API 禁用、Mem0 强制关闭及原数据保留。
- [ ] 冷缓存全流程制稿及三种真实外部客户端端到端验收；单次部署验证不能替代这一项。
- [x] 原生旧数据升级、PostgreSQL 降级后重升级保留原生稿；存在受控稿或回执时拒绝破坏性降级。
- [ ] 真实 Pi、Claude Code、Codex 客户端分别连接并接力的端到端证据。
- [ ] 浏览器手动编辑、flush、Agent/人工交接、选中对象桥接与嵌入入口。
- [ ] 生成任务/槽位、取消 fencing、连接中断后的任务级恢复。
- [ ] 固定修订导出、Worker 租约、下载/交付恢复与真实 Office/WPS 文件验收。
- [ ] 固定字体/对象依赖、对象存储、跨节点恢复、公司模板和中文实际排版验收。
- [ ] 宿主绑定/权限适配、完整生命周期、负载容量与发布验收。

上述勾选仅表示相应本地测试范围，不表示总设计中的 T01–T32 已全部通过，也不表示远端 CI 已运行。未完成能力在 capabilities 中明确为 false。

### Claude Code 本地样例

当前本机使用方式见 [Claude Code 制稿与导出指南](claude-code-ppt.md)。2026-09-21 修正旧演示导出脚本固定访问 `8910`、而 MCP 实际访问 Docker `5002` 的实例错配。新的 `scripts/export_agent_document.py` 读取当前客户端 MCP 配置，稿件与素材从同一服务鉴权获取，验证文档 ID/修订并保留原始快照，复用原有原生渲染代码。旧 `tmp/claude-demo/export_document.py` 保留为转发入口，旧指南改为指向当前文档，不再要求启动演示服务。

失败处理禁止查询演示数据库、猜测文档 ID、导出其他旧稿或重建文稿。操作说明补齐只读/写入分类、JSON 字符串内容、回执复用修订、release 携带 epoch、按需获取少量布局 Schema。该轮仅修复确定的入口错误和操作指引，当时尚未实现批量写入 MCP 工具；后续已实现并部署，见上文“批量调用扩展（2026-09-21）”。外部模型默认推理档位保持不变。

本次完整 Claude 记录为 24 分 42 秒：用户确认后约 8 分 42 秒，Docker 原稿已达到 revision 15、ready；此后约 16 分钟包含错误实例排障、另建稿、参数修复、导出与视觉检查。全程 37 次 MCP 调用的服务端执行累计 2.869 秒，客户端工具等待累计 6.097 秒；这些累计时间包含在总用时内。提纲阶段存在约 194 秒的模型请求间隔，不能凭 MCP 日志将其进一步归因为某一种推理、排队或重试机制。

修复验证：9 项定向测试通过，覆盖配置选定实例、404 无回退/重建、文稿 ID/修订匹配、同实例素材鉴权与缓存、禁止跨源转发凭据及路径越界。保留旧命令进行真实验证：从 Docker 获取原文稿 `62345572-8d6f-46a4-8a52-2c7c1e17810a` 的 revision 15，没有重建或修改稿件，快照与素材读取 0.503 秒、Node 渲染/预览/转换 10.830 秒、Python 端到端导出 11.732 秒。此数字不包括 Claude 内容生成与视觉复核，也不是全流程性能验收。

原稿仍有卡片内容显示不全的问题：视觉抽检第 4 页只显示两张卡片，虽然几何记录为零溢出。这次导出结果用于验证数据源与速度，不能宣称已修复版式或完全通过交付质量验收。该缺陷与实例错配、模型请求延迟分别处理。

2026-09-20 使用 Claude Code 2.1.206（本机配置模型为 Qwen3.7 Plus）连接原有 HTTP MCP 服务，在独立 SQLite 数据库和原生 API-key 鉴权下完成 6 页中文稿件。提纲由用户先确认，客户端随后选择 General 模板，通过原生工具写入、修改并保留备注。素材路径修复后的 13 项工具回归通过。

样例通过现有 `@presenton/export-core` 1.0.30 原生导出器本地接续，使用 PowerPoint 打开并渲染检查；该开源导出器将表格区域栅格化，中文字体在 PowerPoint 中还存在回退，因此不能把本次样例宣称为所有元素均可编辑或与浏览器像素一致。样例和过程证据保留在被 Git 忽略的 `out/claude-ppt-demo/`，调用凭据与原始客户端日志放在独立临时目录，均不提交。

此证据只覆盖一个真实外部客户端的本地样例，不代表三客户端接力、PostgreSQL 部署、浏览器交接、持久任务或固定修订导出接口已经验收。文字排版检查后仍需人工复核事实边界，不能只凭 `completeDocument` 或文件存在判断交付质量。

### 业务任务查询与导出故障（2026-09-21）

`ppt_task(action="status")` 是只读查询，等待期间不持有数据库事务。导出快照保存在 owner/task 隔离文件中，任务进度仅引用其 SHA-256；此字段由服务维护，不加入 MCP 输入。旧内嵌快照仍可读取。数据库与 app-data 必须一起备份。

渲染失败可返回 `snapshot_render_timeout` / `snapshot_render_failed` 和 `phase`，例如 `preview_dom`、`preview_assets`、`export_pptx`。`technical_error` 是停止并报告问题的状态；修复原因后才恢复原任务。文件快照缺失、变化或非法时返回 `export_snapshot_missing` / `export_snapshot_changed` / `export_snapshot_invalid`，任务进入 `export_input_error`，禁止盲目 resume 或重建文稿。非 JSON 上游 5xx 映射为 `upstream_unavailable`。成功后清除旧错误。历史不要求视觉审核的原生客户端可沿用原完成契约；2026-10-10 Truth-Seeker 新任务固定启用 `visualReviewRequired`，全部当前页面必须经实际PNG送达和真实Agent审阅通过，才能completed。生成期同任务Node/Chromium复用，失败不伪造视觉pass。新路径、PDF分页、清单与本机验收边界见[任务渲染与审阅](task-renderer-and-visual-review.md)；历史恢复记录见[实测与恢复记录](mcp-export-recovery-2026-09-21.md)。
