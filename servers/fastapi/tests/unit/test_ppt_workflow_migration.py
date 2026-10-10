import pytest
import asyncio
import uuid
from alembic import command
from sqlalchemy import create_engine, inspect, text

import migrations
from tests.unit.test_migrations import _alembic_config


def test_direct_database_initialization_supports_document_page_versions(tmp_path, monkeypatch):
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from models.sql.template_v2 import TemplateV2
    from models.sql.user import User
    from services import database
    from services.agent_tools import workflow_schemas as inputs
    from tests.unit.test_ppt_workflow import WorkflowClient, start, complete
    from tests.unit.test_slide_ui_chat_tools import _slide_ui
    monkeypatch.delenv("MIGRATE_DATABASE_ON_STARTUP", raising=False)
    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'direct.db').as_posix()}")
        event.listen(engine.sync_engine, "connect", database._enable_sqlite_foreign_keys)
        monkeypatch.setattr(database, "sql_engine", engine)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        try:
            await database.create_db_and_tables()
            await database.create_db_and_tables()
            owner = uuid.uuid4()
            async with sessions() as session:
                session.add(User(id=owner, username=str(owner), hashed_password="unused"))
                session.add(TemplateV2(id="test-template", name="Native V2", is_default=True,
                                      layouts={"layouts": [_slide_ui()]}))
                await session.commit()
            client = WorkflowClient(sessions, owner)
            _, result = await start(client, 1)
            await complete(client, result)
            read = await client.call("read_for_edit", inputs.Read(documentId=result["documentId"]))
            assert len(read["slides"]) == 1 and read["slides"][0]["pageRevision"] > 0
            assert read["phase"] == "ready"
        finally:
            await engine.dispose()
    asyncio.run(run())


def test_existing_coordination_schema_is_not_mistaken_for_workflow_head(tmp_path):
    url = f"sqlite:///{(tmp_path / 'workflow-migration.db').as_posix()}"
    config = _alembic_config(url)
    command.upgrade(config, migrations.REVISION_AGENT_MERGE)
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            schema = inspect(connection)
            assert migrations._infer_revision_from_schema(schema, set(schema.get_table_names()), migrations.REVISION_HEAD) == migrations.REVISION_AGENT_MERGE
        command.upgrade(config, migrations.REVISION_WORKFLOW)
        with engine.connect() as connection:
            schema = inspect(connection)
            assert migrations._infer_revision_from_schema(schema, set(schema.get_table_names()), migrations.REVISION_HEAD) == migrations.REVISION_WORKFLOW
        command.upgrade(config, "head")
        with engine.connect() as connection:
            schema = inspect(connection)
            assert "ppt_workflow_refs" in schema.get_table_names()
            assert "agent_page_revisions" in schema.get_table_names()
            assert "workflow_task_id" in {col["name"] for col in schema.get_columns("agent_documents")}
            assert migrations._infer_revision_from_schema(schema, set(schema.get_table_names()), migrations.REVISION_HEAD) == migrations.REVISION_HEAD
        command.downgrade(config, migrations.REVISION_AGENT_MERGE)
        command.upgrade(config, "head")
    finally:
        engine.dispose()
