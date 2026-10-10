# 元素级稿件生成

`graphite-technical` 增加 `graphite-elements` 布局，renderer 为 `native-elements-v1`。
旧 `graphite-compose/native-blocks-v1` 保持兼容。模板快照嵌入三层 `element_pack`；布局模型与默认模板 Importer 保留该字段。
运行 `python scripts/build-graphite-elements.py` 重建第一方包与布局，不删除其他布局。

## 生成契约

workflow start 返回每个 slot 的真实 Schema。`x-designCatalog` 包含画布、主题、已解析元素样式、可选组合和 sourceHash。
当前石墨包为 1.1.0、14 种样式、一种可选 page_base 组合，sourceHash 为
`ab5170d525d0cfbc7166bd01addfbb21919804f178236005cef76da43992039f`。
页面位置不由该组合决定，Pi 可自行安排全部元素。content 示例：

```json
{
  "title": "什么是 LLM",
  "root": {
    "id": "page",
    "type": "group",
    "position": { "x": 0, "y": 0 },
    "size": { "width": 1280, "height": 720 },
    "children": [
      { "id": "base", "type": "instance", "compositionRef": "page_base" },
      {
        "id": "title", "type": "text", "styleRef": "page_title",
        "position": { "x": 88, "y": 60 },
        "size": { "width": 1000, "height": 70 },
        "content": { "text": "依据上下文，预测下一个 Token" }
      }
    ]
  }
}
```

title 只是元数据，不自动添加可见标题。children 按顺序绘制，组内坐标相对父节点。
text/vector/group/instance/table 可用；首版稿件 Schema 不开放 image 或任意外部素材。
vector.points 相对 vector.position，编译为现有 native 坐标。文字的 props.font 只覆盖指定属性，保留主题 family/color 等。
当前导出器不绘制 vector.end_marker。需要方向时，用独立闭合三角形 vector 放在线段终点；
这保留系统里的元素编辑能力，并让 PDF/PPTX 显示方向。native 编辑新增文字时补齐缺失的 name、decorative、
min_length、max_length，保留已有值和原始 UI 扩展字段，避免编辑器可见但导出失败。
实例展开、主题 token 与样式继承复用 `templates/pack/compiler.py`，没有新的 HTML renderer 或自动布局求解器。

## 检查与保存

提交前检查输入 Schema、真实引用、唯一节点 ID、有限几何、画布越界、最多 180 个展开节点/12 层。
用打包 Noto Sans SC/JetBrains Mono 测量文字，字号 16–96px、行高 1–2.2。
文字容量不足或越界返回可修复的 CompositionError 与 slotId；不静默移动、缩字或删除事实。
这里只做文字与几何容量检查；表格文字容量、复杂变换、元素遮挡、事实与视觉仍须实际预览检查。

保存复用现有 owner scope、固定模板快照、短事务、pageRevision、稳定 slideId 与幂等回执。
就绪页面的连续前缀可先保存；后续页面缺内容时进入 awaiting_content，同任务续作。
生成完成后的局部编辑必须读取当前 native UI/slideId/pageRevision，不能用初始 content 重建覆盖人工编辑。

## 验证边界

`test_element_composition.py` 覆盖位置/主题保留、标题不重复、可选组合、错误修正、分批保存、快照不变及跨 owner 拒绝。
与内容块、包及 workflow 回归共 53 项通过；分批保存加强后元素套件 12 项通过。
修复新增文字元数据后编辑回归 `test_slide_ui_chat_tools.py` 为 63 项通过，元素套件再测 12 项通过。
主站系统 Skill 1.1.0 与生成工具提示优先元素布局。2026-10-05 真实 Pi 在本地 5173/15002 完成
13 页《LLM技术简介·元素组合验证稿》的生成、两轮局部修订、原生编辑保存与重开，最终 revision 25。
系统 PDF/PPTX 均导出接收成功，13 页 PDF 已逐页视觉检查；PPTX 全部 255 个文字框与 native 内容一致，
188 个 vector 导出为 SVG 图片对象，0 个原生 shape/connector。PowerPoint/WPS 重渲染未执行；
PDF 提取存在 FontBBox 警告及部首码位映射限制。主站记录见 `docs/PPT_ELEMENT_COMPOSITION_ACCEPTANCE.md`。

此布局是随代码分发的第一方包。动态 PPTX 导入草稿、工作台试编译和版本保存继续遵循
[三层模板工作台](three-layer-template-packs.md) 的协议，尚未自动发布到主 Agent 生成目录。
原生 UI 可编辑不意味着所有 vector/table 已在 PPTX 中原生导出；PPTX/PDF 必须分别读取、渲染和检查。
