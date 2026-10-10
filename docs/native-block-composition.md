# Native block composition (local Graphite integration)

The optional `renderer: native-blocks-v1` on a Template V2 layout selects the deterministic composition adapter. Existing layouts keep their slot-based schema and rendering. The Graphite template exposes one `graphite-compose` entry; a single entry can generate different layouts.

The existing workflow `submit` takes `pages[].content` with `title`, optional `eyebrow` and `footer`, and `composition`. A node is either a block (`id`, `blockRef`, content fields) or a `row`, `column` or `grid` group with children. The returned JSON schema is authoritative; see [composition_schema.py](../servers/fastapi/templates/v2/composition_schema.py). Supported block IDs are 22 adapters over 12 expressions, not 56 independent renderers. No fixture topic, answer, page count or default items enter the template.

[composition.py](../servers/fastapi/templates/v2/composition.py) measures bundled fonts and calculates positions. ASCII code uses JetBrains Mono; CJK code uses explicit Noto Sans SC runs in both measurement and native output. Relation labels occupy intervals clear of earlier vertical edges. Overflow returns a repairable error before queueing; text is not shrunk automatically. Outline cardinality checking covers explicit layer counts only, not general semantic completeness.

Native components use stable `block-<id>` IDs. Editing retains the existing slide ID, page revision and edit tools. Regenerating a component does not automatically merge user edits. There is no automatic page insertion, image/formula block renderer or connector follow behavior. Tables and charts are native text/vector compositions in the editor; the current PPTX exporter rasterizes some vectors while retaining editable text, so full Office shape/chart/table editability is not claimed.

Validation on the local Truth-Seeker 5173 → 5179 → Presenton15002 chain: 29 targeted tests passed; a real Pi run generated and saved three pages after repairable rejections; visual review and revision-checked component edits produced revision7. System PPTX/PDF exports of that revision were downloaded and inspected; the PDF was rendered and reviewed page by page. The PowerPoint desktop application was not tested. No server deployment or broad business-code changes were made.

Tests: [test_native_composition.py](../servers/fastapi/tests/unit/test_native_composition.py), `test_templates_v2_content.py` and `test_ppt_workflow.py`. Set `COMPOSITION_TEST_FONTS` to the Graphite static font directory and isolate `APP_DATA_DIRECTORY`, `DATABASE_URL` and pytest basetemp.

The accompanying Truth-Seeker workspace contains the full Chinese evidence report at `docs/PPT_COMPOSITION_REPAIR.md`, Skill0.4.0, the content-neutral asset build, original source provenance and local rollback instructions. This workspace contains unrelated pre-existing changes; only the four adapter hook insertions and two new modules were applied to the local running container.
