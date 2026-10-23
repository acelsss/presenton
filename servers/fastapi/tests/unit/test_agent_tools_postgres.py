"""Real PostgreSQL checks, using a fresh isolated schema per case.

Set PRESENTON_TEST_POSTGRES_URL to a disposable database; never use DATABASE_URL
as an implicit fallback. Each client call opens a separate pooled connection.
"""

import asyncio
import os
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from models.sql.agent_document import AgentOperationReceipt
from models.sql.presentation import PresentationModel
from services.agent_tools.schemas import CreateDocument, Mutation
from services.agent_tools.service import AgentDocumentService
from services.chat.tools import ChatTools
from tests.unit.test_agent_tools import Client, create, database, mutate


pytestmark = pytest.mark.skipif(
    not os.environ.get("PRESENTON_TEST_POSTGRES_URL"),
    reason="disposable PostgreSQL is not configured",
)


def test_concurrent_create_commits_one_document_and_receipt(tmp_path):
    async def run():
        async with database(tmp_path) as (sessions, clients):
            client = clients[0]
            await client.call("open_session", "create-race")
            request = CreateDocument(
                operationId=uuid.uuid4(), title="concurrent create"
            )
            results = await asyncio.wait_for(
                asyncio.gather(*[client.call("create", request) for _ in range(5)]), 15
            )
            assert len({result["documentId"] for result in results}) == 1
            assert sum(not result.get("replayed", False) for result in results) == 1
            async with sessions() as session:
                AgentDocumentService(session, client.owner)
                assert len(list(await session.scalars(select(PresentationModel)))) == 1
                assert (
                    len(list(await session.scalars(select(AgentOperationReceipt)))) == 1
                )

    asyncio.run(run())


def test_same_operation_retries_execute_native_handler_once(tmp_path, monkeypatch):
    async def run():
        async with database(tmp_path) as (_, clients):
            client = clients[0]
            doc, epoch = await create(client)
            entered, finish = asyncio.Event(), asyncio.Event()
            native = ChatTools.execute_validated
            calls = 0

            async def controlled(self, *args):
                nonlocal calls
                calls += 1
                result = await native(self, *args)
                entered.set()
                await asyncio.wait_for(finish.wait(), 5)
                return result

            monkeypatch.setattr(ChatTools, "execute_validated", controlled)
            request = Mutation(
                operationId=uuid.uuid4(),
                expectedRevision=0,
                writerEpoch=epoch,
                tool="addOutline",
                arguments={"content": "# One", "index": None},
            )
            first = asyncio.create_task(client.call("mutate", doc, request))
            await asyncio.wait_for(entered.wait(), 5)
            second = asyncio.create_task(client.call("mutate", doc, request))
            done, _ = await asyncio.wait([second], timeout=0.1)
            assert not done
            finish.set()
            results = await asyncio.wait_for(asyncio.gather(first, second), 5)
            assert calls == 1
            assert results[0]["revision"] == results[1]["revision"] == 1
            assert results[1]["replayed"] is True

    asyncio.run(run())


def test_two_mutations_on_same_revision_cannot_overwrite(tmp_path):
    async def run():
        async with database(tmp_path) as (_, clients):
            client = clients[0]
            doc, epoch = await create(client)
            results = await asyncio.wait_for(
                asyncio.gather(
                    *[
                        mutate(
                            client,
                            doc,
                            epoch,
                            "addOutline",
                            {"content": f"# {label}", "index": None},
                            revision=0,
                        )
                        for label in ["A", "B"]
                    ],
                    return_exceptions=True,
                ),
                10,
            )
            successes = [result for result in results if isinstance(result, dict)]
            conflicts = [
                result for result in results if isinstance(result, HTTPException)
            ]
            assert len(successes) == len(conflicts) == 1
            assert conflicts[0].detail["code"] == "revision_conflict"
            snapshot = await client.call("read", doc)
            assert (
                snapshot["revision"] == 1 and len(snapshot["outlines"]["slides"]) == 1
            )

    asyncio.run(run())


def test_read_waits_for_atomic_ui_revision_and_stale_write_is_rejected(
    tmp_path, monkeypatch
):
    async def run():
        async with database(tmp_path) as (sessions, clients):
            client = clients[0]
            doc, epoch = await create(client)
            other = Client(sessions, client.owner)
            await other.call("open_session", "second-instance")
            entered, finish = asyncio.Event(), asyncio.Event()
            native = ChatTools.execute_validated

            async def controlled(self, *args):
                result = await native(self, *args)
                entered.set()
                await asyncio.wait_for(finish.wait(), 5)
                return result

            monkeypatch.setattr(ChatTools, "execute_validated", controlled)
            writer = asyncio.create_task(
                mutate(
                    client,
                    doc,
                    epoch,
                    "addOutline",
                    {"content": "# Atomic", "index": None},
                    revision=0,
                )
            )
            await asyncio.wait_for(entered.wait(), 5)
            reader = asyncio.create_task(other.call("read", doc))
            stale = asyncio.create_task(
                mutate(other, doc, epoch, "addOutline", {"content": "# Stale"}, revision=0)
            )
            done, _ = await asyncio.wait([reader, stale], timeout=0.1)
            assert not done
            finish.set()
            await writer
            snapshot = await reader
            assert (
                snapshot["revision"] == 1
                and snapshot["outlines"]["slides"][0]["content"] == "# Atomic"
            )
            with pytest.raises(HTTPException) as caught:
                await stale
            assert caught.value.detail["code"] == "revision_conflict"

    asyncio.run(run())


def test_postgres_upgrade_backfill_downgrade_and_reupgrade_preserve_native_data():
    from alembic import command
    from sqlalchemy import create_engine, inspect, text
    from sqlalchemy.engine import make_url
    import migrations
    from tests.unit.test_migrations import _alembic_config

    schema = "agent_migration_" + uuid.uuid4().hex
    url = make_url(os.environ["PRESENTON_TEST_POSTGRES_URL"]).set(
        drivername="postgresql+psycopg"
    )
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = url.update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_engine(scoped)
    try:
        config = _alembic_config(
            scoped.render_as_string(hide_password=False).replace("%", "%%")
        )
        command.upgrade(config, migrations.REVISION_UNIFIED_API_KEYS)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO presentations (id, version, content, n_slides, language, created_at, updated_at) VALUES (:id, 'v2-standard', 'native draft', 1, 'Chinese', now(), now())"
                ),
                {"id": uuid.uuid4()},
            )
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT agent_managed FROM presentations"))
                is False
            )
            inspector = inspect(connection)
            assert {
                "agent_documents",
                "agent_caller_sessions",
                "agent_operation_receipts",
            }.issubset(inspector.get_table_names())
            assert any(
                constraint["name"] == "uq_agent_operation_owner_key"
                for constraint in inspector.get_unique_constraints(
                    "agent_operation_receipts"
                )
            )
        command.downgrade(config, migrations.REVISION_UNIFIED_API_KEYS)
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT content FROM presentations"))
                == "native draft"
            )
        with engine.begin() as connection:
            connection.execute(text("UPDATE presentations SET agent_managed = true"))
        with pytest.raises(RuntimeError, match="Managed documents/receipts exist"):
            command.downgrade(config, migrations.REVISION_UNIFIED_API_KEYS)
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
