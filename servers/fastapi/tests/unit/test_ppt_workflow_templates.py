import asyncio
import copy
from types import SimpleNamespace
from threading import Barrier

import pytest
from fastapi import HTTPException
from sqlalchemy import event

from models.sql.ppt_workflow import PptWorkflowRef
from models.sql.template_v2 import TemplateV2
from services.agent_tools import workflow_schemas as inputs
from services.agent_tools.workflow import WorkflowService
from tests.unit.test_agent_tools import database
from tests.unit.test_ppt_workflow import WorkflowClient


def test_template_paging_reads_only_selected_layouts_and_keeps_order_and_scope(tmp_path):
    async def run():
        async with database(tmp_path) as (sessions, owners):
            async with sessions() as session:
                template = await session.get(TemplateV2, "test-template")
                layout = copy.deepcopy(template.layouts)
                session.add_all([TemplateV2(id=f"page-{i}", name=f"Style {i}", is_default=True,
                    layouts=layout, raw_layouts={"unused": "x" * 100_000},
                    components={"unused": "y" * 100_000}) for i in range(6)])
                session.add(TemplateV2(id="own", name="Style owned", owner_id=owners[0].owner, layouts=layout))
                session.add(TemplateV2(id="foreign", name="Style foreign", owner_id=owners[1].owner, layouts=layout))
                for name, value in [("empty", {"layouts": []}), ("missing", {}), ("null", None)]:
                    session.add(TemplateV2(id=name, name=name, is_default=True, layouts=value))
                await session.commit()
            async with sessions() as session:
                columns = []
                def capture(conn, cursor, statement, parameters, context, executemany):
                    if context.compiled is not None:
                        columns.extend(getattr(context.compiled.statement, "selected_columns", ()))
                engine = session.bind.sync_engine
                event.listen(engine, "before_cursor_execute", capture)
                try:
                    result = await WorkflowService(session, owners[0].owner, "test").prepare(inputs.Prepare(
                        title="Paging", outline=["One"], confirmed=True, style="Style"))
                finally:
                    event.remove(engine, "before_cursor_execute", capture)
                assert not any(isinstance(value, TemplateV2) for value in session.identity_map.values())
                assert not any(column.shares_lineage(TemplateV2.__table__.c[field])
                    for column in columns for field in ["raw_layouts", "components", "merged_components", "assets"])
            assert [item["name"] for item in result["templates"]] == ["Style owned", "Style 0", "Style 1", "Style 2"]
            assert result["nextTemplateOffset"] == 4
            client = WorkflowClient(sessions, owners[0].owner)
            second = await client.call("prepare", inputs.Prepare(title="Paging", outline=["One"],
                confirmed=True, style="Style", templateOffset=4))
            assert [item["name"] for item in second["templates"]] == ["Style 3", "Style 4", "Style 5", "Native V2"]
            assert second["nextTemplateOffset"] is None
            with pytest.raises(HTTPException) as error:
                await client.call("prepare", inputs.Prepare(title="Paging", outline=["One"], confirmed=True, templateOffset=8))
            assert error.value.detail["code"] == "no_accessible_templates_at_offset"
    asyncio.run(run())


def test_template_preview_projects_thumbnail_and_rechecks_native_permission(tmp_path):
    async def run():
        async with database(tmp_path) as (sessions, owners):
            client = WorkflowClient(sessions, owners[0].owner)
            prepared = await client.call("prepare", inputs.Prepare(title="Preview", outline=["One"], confirmed=True))
            request = SimpleNamespace(preparation_ref=prepared["preparationRef"], template_ref=prepared["templates"][0]["templateRef"])
            async with sessions() as session:
                template = await session.get(TemplateV2, "test-template")
                template.assets = {"thumbnail": "/static/template.png", "unused": "x" * 100_000}
                await session.commit()
            async with sessions() as session:
                columns = []
                def capture(conn, cursor, statement, parameters, context, executemany):
                    if context.compiled is not None:
                        columns.extend(getattr(context.compiled.statement, "selected_columns", ()))
                engine = session.bind.sync_engine
                event.listen(engine, "before_cursor_execute", capture)
                try:
                    shown = await WorkflowService(session, owners[0].owner, "test").preview(request)
                finally:
                    event.remove(engine, "before_cursor_execute", capture)
                assert shown == {"thumbnail": "/static/template.png"}
                assert not session.identity_map
                assert not any(column.shares_lineage(full) for column in columns for full in [
                    PptWorkflowRef.__table__.c.data, TemplateV2.__table__.c.layouts, TemplateV2.__table__.c.assets])
            with pytest.raises(HTTPException) as error:
                await WorkflowClient(sessions, owners[1].owner).call("preview", request)
            assert error.value.detail["code"] == "workflow_reference_not_found"
            with pytest.raises(HTTPException) as error:
                await client.call("preview", SimpleNamespace(preparation_ref=request.preparation_ref, template_ref="missing"))
            assert error.value.detail["code"] == "template_not_in_preparation"
            async with sessions() as session:
                template = await session.get(TemplateV2, "test-template")
                template.is_default = False
                template.owner_id = owners[1].owner
                await session.commit()
            with pytest.raises(HTTPException) as error:
                await client.call("preview", request)
            assert error.value.detail["code"] == "template_permission_revoked"
    asyncio.run(run())


def test_template_file_checks_overlap_without_changing_owner_or_pinned_hashes(tmp_path, monkeypatch):
    import services.agent_tools.workflow_export as exports
    async def run():
        async with database(tmp_path) as (sessions, owners):
            async with sessions() as session:
                template = await session.get(TemplateV2, "test-template")
                session.add(TemplateV2(id="second", name="Second", is_default=True, layouts=copy.deepcopy(template.layouts)))
                await session.commit()
            rendezvous = Barrier(2)
            def fingerprints(owner, values):
                assert owner == owners[0].owner
                # Sequential execution times out here; both file checks must start.
                rendezvous.wait(timeout=3)
                return {"/static/shared.png": values[0]["name"]}
            monkeypatch.setattr(exports, "asset_fingerprints", fingerprints)
            result = await WorkflowClient(sessions, owners[0].owner).call("prepare", inputs.Prepare(
                title="Concurrent checks", outline=["One"], confirmed=True))
            async with sessions() as session:
                ref = await session.get(PptWorkflowRef, result["preparationRef"])
                assert len(ref.data["templates"]) == 2
                for item in ref.data["templates"].values():
                    assert item["layout"]["_agentAssetHashes"] == {"/static/shared.png": f"custom-{item['id']}"}
    asyncio.run(run())
