import uuid

from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

import migrations
from tests.unit.test_migrations import _alembic_config


def test_guided_database_merges_without_losing_legacy_data(tmp_path):
    url = f"sqlite:///{(tmp_path / 'guided.db').as_posix()}"
    config = _alembic_config(url)
    command.upgrade(config, migrations.REVISION_LEGACY_AGENT_PLANNING)
    engine = create_engine(url)
    document_id = uuid.uuid4().hex
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO presentations (id, version, content, n_slides, language, created_at, updated_at) "
                "VALUES (:id, 'v2-standard', 'existing manuscript', 1, 'Chinese', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ), {"id": document_id})
            connection.execute(text(
                "INSERT INTO agent_ppt_jobs (id, scope_hash, create_key, create_hash, owner_id, presentation_id, "
                "state, revision, fence, writer_until, handoff_required, expires_at, cleanup_state, planning) "
                "VALUES (:id, :scope, 'existing', :scope, :owner, :document, 'ready', 7, 3, 0, false, 0, 'active', :plan)"
            ), {"id": uuid.uuid4().hex, "scope": "a" * 64, "owner": uuid.uuid4().hex,
                "document": document_id, "plan": '{"confirmed":true}'})
            before = connection.execute(text("SELECT * FROM agent_ppt_jobs")).fetchall()
        command.upgrade(config, "head")
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT * FROM agent_ppt_jobs")).fetchall() == before
            assert connection.execute(text("SELECT content, agent_managed FROM presentations")).one() == (
                "existing manuscript", False
            )
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all() == [migrations.REVISION_HEAD]
            schema = inspect(connection)
            assert migrations._infer_revision_from_schema(
                schema, set(schema.get_table_names()), migrations.REVISION_HEAD
            ) == migrations.REVISION_HEAD
        assert ScriptDirectory.from_config(config).get_heads() == [migrations.REVISION_HEAD]
    finally:
        engine.dispose()
