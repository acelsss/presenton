import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text

import migrations
from tests.unit.test_migrations import _alembic_config


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
