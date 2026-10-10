# 三层模板包与检验工作台：首版实现

本轮实现真实 PPTX → 三层包 → 数据库版本 → 双模式 native UI 编译 → 检验工作台的本地闭环。入口为 `/template-inspector`，现有模板预览页标题栏增加“模板检验”入口。工作台不依赖 LLM 配置，但仍要求登录，不绕过账号与 owner 隔离。

## 执行计划与完成范围

| 阶段 | 首版交付 | 状态 |
| --- | --- | --- |
| 源文件解析 | python-pptx/OOXML，原生文字、矩形、圆角、椭圆、自由路径、图片、表格、资源和来源 | 已实现 |
| 三层模板 | 主题 token、样式 recipe、布局、组合候选、页面模式、绑定和稳定 ID | 已实现 |
| 解析与校验 | 严格外层 Schema、token、继承、循环引用、ID、绑定、native 模型、画布越界诊断 | 已实现 |
| 版本保存 | owner 范围、不可变新版本、源哈希、资源固定哈希、报告 | 已实现 |
| 完整检验界面 | 全目录、逐项预览、JSON、Schema、路径映射、诊断、试填、元素位置修改、新草稿、刷新回读 | 已实现 |
| 主站生成接入 | Truth-Seeker/Pi 选择新包、冻结版本、生成、写入稿件 | 后续阶段 |
| 发布验收 | 文本测量、编辑器稿件保存、PPTX/PDF 导出、全部高级结构能力 | 未完成，保持草稿 |

现有 Smart 模式和 Graphite 组合实现保持原流程。新 `generationMode` 目前是模板包编译接口的显式参数，不代表主站或全部 native 生成入口已经迁移。

## 包格式

Schema 的权威实现为 `templates/pack/models.py`，认证后可读取 `GET /api/v1/ppt/template-packs/schema`。

```json
{
  "schemaVersion": "1.0",
  "id": "example",
  "version": "1",
  "name": "示例",
  "supportedModes": ["template", "composition"],
  "canvas": {"width": 1280, "height": 720},
  "defaultThemeRef": "light",
  "themes": [{"id": "light", "name": "浅色", "tokens": {"colors": {"text": "#111827"}, "fonts": {"body": "Arial"}, "typography": {"title": 34}, "spacing": {}, "radii": {}}}],
  "elementStyles": [{"id": "title", "type": "text", "name": "标题", "properties": {"font": {"family": "$fonts.body", "size": "$typography.title", "color": "$colors.text"}}}],
  "compositions": [{"id": "title_block", "name": "标题组合", "root": {"id": "title_node", "type": "text", "position": {"x": 64, "y": 36}, "size": {"width": 1000, "height": 70}, "styleRef": "title", "content": {"text": "示例标题"}}}],
  "pagePatterns": [{"id": "title_page", "name": "标题页模式", "compositionRef": "title_block"}],
  "layouts": [{"id": "cover", "name": "封面", "root": {"id": "cover_title", "type": "text", "position": {"x": 64, "y": 36}, "size": {"width": 1000, "height": 70}, "styleRef": "title", "content": {"text": "示例标题"}}, "bindings": {"title": {"nodeId": "cover_title", "property": "content.text", "valueType": "string", "required": true}}}],
  "assets": [],
  "provenance": {},
  "diagnostics": []
}
```

节点首版支持 `text/vector/image/table/group/instance`。`props` 是已有 native 元素属性的严格适配入口，未知属性会拒绝。`instance.compositionRef` 展开后用实例 ID 前缀生成稳定子 ID。同一个包的节点树和 native UI 路径之间有 `nodePaths` 映射。

坐标使用逻辑 px，子节点相对父节点。vector 的局部 points 在编译时加上节点位置。生成次序沿用 children 顺序。主题 token 仅支持两段引用，例如 `$colors.text`；字面量 token 不支持递归表达式。样式继承仅允许同类元素，不执行脚本。

当前 native 画布固定 1280×720；其他比例可以解析并保留草稿目录，但编译会明确拒绝，不错误展示成已支持。图片只接受包内 `asset:<id>`，由版本资源接口解析，不能通过试验 JSON 插入任意外部 URL。

## 两种编译调用

模板填充：服务端从 layout 和 bindings 生成内容 Schema。未提供 content 时使用源内容作为预览样例；显式提供时执行完整结构校验。

```json
{"generationMode":"template","ref":"cover","content":{"title":"理解大语言模型"}}
```

元素组合：可引用现有组合，也可提交新元素树，自行调整位置、内容、样式引用和组合。首版使用绝对/相对坐标；自动布局求解不在当前实现内。

```json
{"generationMode":"composition","root":{"id":"custom_page","type":"group","children":[{"id":"heading","type":"text","position":{"x":80,"y":60},"size":{"width":900,"height":80},"styleRef":"title","content":{"text":"新的页面组合"}}]}}
```

`POST /api/v1/ppt/template-packs/{version_id}/compile` 返回 `ui/resolvedRoot/nodePaths/contentSchema/sourceHash/compilerVersion/findings`。返回的 ui 经过现有 `SlideLayout` 模型校验，工作台使用实际 `TemplateV2KonvaSlide`，没有另造简化预览 renderer。

## 数据库与 API

- `template_v2` 保留模板身份和 owner，新导入草稿的 `layouts` 为空，避免未经视觉/导出验收的包进入旧生成目录。
- `template_pack_versions` 保存 `template_id/owner_id/version/source_hash/source/validation_report/status/asset_directory/created_at`，每次编辑创建新行，没有覆盖旧版本的更新接口。
- 资源写入 `app_data/uploads/users/{owner}/template-packs/{version_id}/`，JSON 只保存资产元数据。新版本复用同一固定资源清单；不能通过包编辑替换资源或身份。
- 所有版本读写和资源读取同时核对版本 owner 与模板 owner，跨用户访问返回 404。资源读取验证大小与 SHA-256，使用私有缓存策略；列表分页返回总数。
- `assets.templatePackVersion` 指向初次导入版本；新草稿在工作台按版本 ID 选择，尚无“发布当前版本”的操作。
- Alembic 新 revision 为 `f2b358d7ea64`，直接建表路径也注册新表。迁移推断区别旧页面版本 head 与新包表，不把旧数据库误认作已完成新迁移。

| API | 用途 |
| --- | --- |
| `GET /template-packs` | 当前账号的分页版本清单 |
| `POST /template-packs/import` | multipart 字段 `pptx_file`；最多 40 MiB、100 页，展开 ZIP 有大小与条目上限 |
| `GET /template-packs/{id}` | 固定版本源包、报告与完整资源清单 |
| `POST /template-packs/{id}/validate` | 重新编译并核验资源，不修改旧版本 |
| `POST /template-packs/{id}/compile` | 两种模式的隔离试验 |
| `POST /template-packs/{id}/versions` | `expectedSourceHash + source` 保存新的草稿 |
| `GET /template-packs/{id}/assets/{assetId}` | owner 保护并校验哈希后的原资源 |

所有路径统一位于 `/api/v1/ppt` 下。报告的结构通过只证明解析与编译，`visual/editSave/pptxExport/pdfExport` 保持 `unrun`，不能当作出版或生成稿件验收。工作台另存的是模板定义新版本，尚不等同于原生稿件编辑器保存验收。

## 真实样例与质量差异

源文件 `LLM与JEV技术分享.pptx`：7 页，212 个源形状，原文件 SHA-256 为 `80a7b9dda88d824c5fd0ad12126e257fdf0d5ce35a4ddfb83c25c35592e44bd7`。本机样例包和验收证据位于 `app_data/template-pack-reference/`，账号/会话文件不属于交付文件，不提交。

自动提取的组合包括每页完整组合及由几何包围关系形成的“空间组合候选”。它们不是已经人工确认的时间轴、列表或流程组件。主题颜色、字体、字号和圆角来自源稿；没有推断出可靠语义的间距 token 保持空字典。

源稿部分文本框允许文字超出不换行边界。native renderer 固定 word wrap；首版在导入时根据字符宽度估计扩展这些框，保留源框坐标与 `text_wrap_adapted` 诊断。这不是精确字体测量，邻近元素和新长文本必须继续检查。

Bezier 曲线采样成可编辑折线并标记 `path_curve_sampled`。视频保留原文件和封面，native ui 仅显示封面。表格保留内容、背景、行高和列宽；边框与 padding 仍受 native 默认样式影响并有诊断。嵌套 group 变换、图片 crop、合并单元格及未知形状出现时保持诊断，不宣称无损还原。

## 验证方法

后端在独立 SQLite 与临时资产目录运行 `test_template_packs.py`、`test_template_pack_api.py`，覆盖完整目录、源哈希、结构失败、继承、循环依赖、绑定、两种模式、坐标、资源与跨 owner 拒绝、旧版本不可变和草稿不进入旧目录。另运行既有模板 API、元素模型和迁移测试。

前端运行 `tsc --noEmit` 和 renderer/component bounds 回归测试；新表格检查验证行列比例与背景。`tests/template-pack-inspector.browser.test.mjs` 是显式启用的真实服务验收，未启用时 skip；启用需要：

```text
PACK_INSPECTOR_URL          已启动工作台 URL（含 version 与本地 fastapiUrl）
PACK_PLAYWRIGHT_PACKAGE     已安装 @playwright/test 的 package.json 路径
PACK_COOKIE_FILE           本机私有登录文件，格式 {"cookies": {"cookieName": "value"}}
PACK_DETAIL_FILE           真实导入响应 JSON
PACK_EVIDENCE_DIRECTORY    非公开证据目录
```

测试遍历全部目录、使用实际 canvas 渲染、试填、调整坐标、保存新版本、刷新回读，以及 390px 溢出检查。cookie 不进入日志或证据。测试不生成源 PPTX，也不模拟后端结果。样例验收截图和 `browser-acceptance.json` 在上述本机证据目录。

本机启动采用隔离端口 3083/5083 和独立 SQLite，保留认证；Next dev 使用 `--webpack`，因为本机现有 node_modules junction 在 Turbopack 的项目根限制下启动失败。没有修改依赖或为本轮部署现有 Docker/公司服务。PowerPoint COM 无法打开源稿，视觉对照使用已安装 Artifact runtime 导入渲染；这不代表 PowerPoint 本身的兼容性验收通过。

### 2026-10-05 本机验收结果

最终样例版本为 `d9f70f6e-9684-4ce8-a689-5dec8a7816bf`，源包哈希 `dab209b6c2c44427834581f81cedd2e4e7c03fcc976c1d9d760b7739635ce526`。全目录包含 1 套主题、60 种元素样式、22 个组合候选、7 个页面模式、7 个固定布局和 7 项资源。主题包含 4 色、3 种字体、14 个字号 token 和 14 个圆角 token。间距语义尚未推断。

后端定向及模板/迁移回归 101/101；前端 renderer/bounds 8/8；类型检查通过。真实 Chromium 验收 1/1，遍历全目录 104 个条目，验证试填、元素移动、新草稿保存/刷新、390px 无横向溢出，pageErrors 为空。全 7 页已有 native canvas 截图，原稿 SHA-256 回查一致。

结构与资源哈希检查通过；源稿不换行框适配、Bezier 采样、视频封面和表格默认边框产生 87 条诊断，保持可查。视觉仅完成当前样例人工对照，未形成可发布质量证书。新草稿另存的通过不能替代稿件编辑器保存、PPTX/PDF 导出验收，这些发布状态继续为 unrun。

## Truth-Seeker 系统 Skill（2026-10-05）

后续元素生成增量见 [元素级稿件生成](element-generation.md)：第一方石墨包接入真实 workflow，系统 Skill 升至 1.1.0。
下方 1.0.0 与未执行状态为此前检验工作台/协议 smoke 的历史记录，不代表新的稿件验收结果。

按用户要求改为 Truth-Seeker 系统 Pack，审核源位于主站 `infra/pi-skills/presenton-template-pack/1.0.0/`。展示名“PPT 三层模板”，成员 `presenton-template-pack`，通过既有受控 Importer 登记 Registry，供原生 Pi 加载；默认开启、用户可关闭。在系统技能选择器选择该成员后发起任务，后端改写为原生 `/skill:presenton-template-pack`。任务示例：

```text
检查参考模板 JSON 的三层结构，并给出模板填充与元素组合试验方案。
```

系统成员包含流程、Schema/模式参考和最小协议源包，不使用个人 Codex 文件、不读取 Cookie、不通过脚本直连后台。主 Agent 当前没有模板包 HTTP 对应 import/compile/versions 工具时，可通过资源工具保存 JSON 方案，并明确未编译/入库；稿件生成与编辑继续使用系统已有 ppt 工具。系统 Pack 的发现、开关和 14 项 API/Importer 定向检查通过，真实 Pi 显式/自动读取通过。当前只激活开发 Registry，未部署公司环境。

前一轮误交付的 Codex 版已撤下，其标准库 CLI 和参考保存在主站忽略的 `.local/template-pack-codex-original/`，属于开发验收历史工具，不随系统成员加载。下述 CLI 验收记录保留：脚本支持 list/schema/import/inspect/inventory/validate/compile/new-version，输出仅写新文件，不自动重试、不跟随重定向，新旧版本回读校验；这些历史检查不能替代系统工具接入。

CLI 测试 7/7，覆盖文件不覆盖、重定向拒绝、中文上传文件名、断连不重试、认证值不进入错误日志、创建结果保留、过期 hash 拒绝与旧版本不可变检查。真实隔离服务完成 12 项调用：导入、回读、全目录、Schema、分页列表、结构/资源校验、默认模板编译、试填、引用组合、自定义位置移动、新版本保存和新版本编译；文档中的 4 个编译示例通过当前编译器。

本轮脚本验收证据在 `app_data/template-pack-reference/skill-validation/smoke-20261005-011844/acceptance.json`。隔离验收导入版本为 `e3b1e0e9-cb26-42d7-8ca6-d5db26726eb1`，位置修改后的新草稿为 `6d360998-5c54-4cbb-a22f-57126222259c`；原参考 PPTX 哈希不变。这两份是脚本验收草稿，前述人工视觉样例版本不变。

系统源包 1.0.0 的 digest 为 `9e5b354ff7df136091437e83894b2d8a051610db8347d18e9cc0db8b9236c5e0`；真实 Pi 证据在主站 `.local/template-pack-system-smoke/80fe3505-1b70-4f6f-8fc5-18abff326cee/acceptance.json`。此次 Skill 验证未重复浏览器视觉验收，也未执行模板包主 Agent 业务工具、稿件编辑保存或 PPTX/PDF 导出，这些验收继续保持 unrun。主站扩展回归的 Windows/环境失败见其 `docs/TESTING.md`，不能把本轮定向通过声明为全量回归通过。
