import asyncio
import json
import os
import uuid

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from services.agent_tools.schemas import BatchMutation, ReadBatch, ToolRequest
from services.agent_tools.service import AgentDocumentService
from tests.unit.test_agent_tools import create, database, mutate, slide_content


def batch(epoch, revision, operations, operation_id=None):
    return BatchMutation(
        operationId=operation_id or uuid.uuid4(), expectedRevision=revision,
        writerEpoch=epoch, operations=operations,
    )


def outlines(count=6):
    return [ToolRequest(tool='addOutline', arguments={'content': f'# Page {i + 1}'})
            for i in range(count)]


async def setup(client, count=6):
    doc, epoch = await create(client)
    result = await client.call('mutate', doc, batch(epoch, 0, [
        *outlines(count), ToolRequest(tool='confirmOutline', arguments={'confirmed': True}),
    ]))
    assert result['status'] == 'applied' and result['revision'] == 1
    await mutate(client, doc, epoch, 'selectTemplate', {'templateId': 'test-template'}, revision=1)
    return doc, epoch


def pages(count=6):
    return [ToolRequest(tool='saveSlide', arguments={
        'index': i, 'layoutId': 'intro', 'content': json.dumps(slide_content(f'Page {i + 1}')),
        'replaceOldSlideAtIndex': False,
    }) for i in range(count)]


def test_six_page_native_workflow_uses_two_atomic_batches(tmp_path):
    async def run():
        async with database(tmp_path) as (_, clients):
            client = clients[0]
            doc, epoch = await setup(client)
            reads = await client.call('read_tools', doc, ReadBatch(operations=[
                ToolRequest(tool='getAvailableLayouts'),
                ToolRequest(tool='getContentSchemaFromLayoutId', arguments={'layoutId': 'intro'}),
            ]))
            assert reads['revision'] == 2 and len(reads['results']) == 2
            request = batch(epoch, 2, [*pages(), ToolRequest(tool='completeDocument')])
            result = await client.call('mutate', doc, request)
            assert result['revision'] == 3 and result['status'] == 'applied'
            assert len(result['result']['results']) == 7
            snapshot = await client.call('read', doc)
            assert snapshot['phase'] == 'ready' and len(snapshot['slides']) == 6
            assert [s['index'] for s in snapshot['slides']] == list(range(6))
            assert all(s['speakerNote'] == '讲稿' for s in snapshot['slides'])
            # An uncertain-response retry uses its persisted receipt, without holding any writer permission.
            replay = await client.call('mutate', doc, request)
            assert replay['replayed'] and replay['revision'] == 3
            assert len((await client.call('read', doc))['slides']) == 6

    asyncio.run(run())


def test_late_rejection_rolls_back_prior_pages_and_has_one_replayable_receipt(tmp_path):
    async def run():
        async with database(tmp_path) as (_, clients):
            client = clients[0]
            doc, epoch = await setup(client, 2)
            operations = pages(2)
            operations[1].arguments['content'] = '{}'  # Required native content is missing.
            request = batch(epoch, 2, operations)
            result = await client.call('mutate', doc, request)
            assert result['status'] == 'rejected' and result['revision'] == 2
            assert result['result']['failedIndex'] == 1
            snapshot = await client.call('read', doc)
            assert snapshot['slides'] == [] and snapshot['phase'] == 'composing'
            assert (await client.call('mutate', doc, request))['replayed']
            operations[1].arguments['content'] = json.dumps(slide_content())
            with pytest.raises(HTTPException) as caught:
                await client.call('mutate', doc, request)
            assert caught.value.detail['code'] == 'operation_id_reused'

    asyncio.run(run())


@pytest.mark.parametrize('failure', ['handler', 'receipt'])
def test_batch_failure_never_commits_a_partial_manuscript(tmp_path, monkeypatch, failure):
    async def run():
        async with database(tmp_path) as (_, clients):
            client = clients[0]
            doc, epoch = await setup(client, 2)
            original = AgentDocumentService._apply_tool

            async def injected(service, state, presentation, operation):
                result = await original(service, state, presentation, operation)
                if operation.arguments['index'] == 1:
                    if failure == 'handler':
                        raise RuntimeError('handler failure')
                return result

            async def failed_receipt(*args, **kwargs):
                raise RuntimeError('receipt failure')

            monkeypatch.setattr(AgentDocumentService, '_apply_tool', injected)
            if failure == 'receipt':
                monkeypatch.setattr(AgentDocumentService, '_record', failed_receipt)
            request = batch(epoch, 2, pages(2))
            with pytest.raises((RuntimeError, HTTPException)):
                await client.call('mutate', doc, request)
            snapshot = await client.call('read', doc)
            assert snapshot['revision'] == 2 and snapshot['slides'] == []
            assert await client.call('receipt', request.operation_id) is None

    asyncio.run(run())


def test_batch_owner_revision_and_tool_admission_remain_enforced(tmp_path):
    async def run():
        async with database(tmp_path) as (_, clients):
            client, other = clients
            doc, epoch = await create(client)
            await other.call('open_session', 'other')
            with pytest.raises(HTTPException) as caught:
                await other.call('mutate', doc, batch(epoch, 0, outlines()))
            assert caught.value.status_code == 404
            for request, code in [
                (batch(epoch, 9, outlines()), 'revision_conflict'),
                (batch(epoch, 0, [*outlines(), ToolRequest(tool='generateAssets')]), 'write_tool_not_available'),
            ]:
                with pytest.raises(HTTPException) as caught:
                    await client.call('mutate', doc, request)
                assert caught.value.detail['code'] == code
            with pytest.raises(HTTPException) as caught:
                await client.call('read_tools', doc, ReadBatch(operations=outlines(1)))
            assert caught.value.detail['code'] == 'read_tool_not_available'
            snapshot = await client.call('read', doc)
            assert snapshot['revision'] == 0 and not snapshot['outlines']['slides']

    asyncio.run(run())


@pytest.mark.parametrize('size', [0, 21])
def test_batch_size_is_bounded(size):
    with pytest.raises(ValidationError):
        batch(1, 0, outlines(size))


def test_json_encoded_operations_use_the_same_validation_and_receipt(tmp_path):
    async def run():
        async with database(tmp_path) as (_, clients):
            client = clients[0]
            doc, epoch = await create(client)
            request = batch(epoch, 0, outlines())
            encoded = request.model_dump(mode='json', by_alias=True)
            encoded['operations'] = json.dumps(encoded['operations'])
            response = await client.call('mutate', doc, BatchMutation.model_validate(encoded))
            assert response['status'] == 'applied' and response['revision'] == 1
            replay = await client.call('mutate', doc, request)
            assert replay['replayed'] and replay['revision'] == 1
            assert len((await client.call('read', doc))['outlines']['slides']) == 6

    asyncio.run(run())


@pytest.mark.parametrize('encoded', ['{}', '[]', 'null', '[invalid', json.dumps([{'tool': 'addOutline'}] * 21)])
def test_json_encoded_operations_do_not_bypass_array_constraints(encoded):
    with pytest.raises(ValidationError):
        BatchMutation(operationId=uuid.uuid4(), expectedRevision=0, writerEpoch=1, operations=encoded)


@pytest.mark.skipif(not os.environ.get('PRESENTON_TEST_POSTGRES_URL'), reason='disposable PostgreSQL required')
@pytest.mark.parametrize('same_id', [False, True])
def test_concurrent_batches_preserve_revision_and_idempotency(tmp_path, same_id):
    async def run():
        async with database(tmp_path) as (_, clients):
            client = clients[0]
            doc, epoch = await create(client)
            first = batch(epoch, 0, outlines())
            second = first if same_id else batch(epoch, 0, outlines())
            results = await asyncio.gather(
                client.call('mutate', doc, first), client.call('mutate', doc, second),
                return_exceptions=True,
            )
            if same_id:
                assert all(not isinstance(x, Exception) for x in results)
                assert sum(x.get('replayed', False) for x in results) == 1
            else:
                errors = [x for x in results if isinstance(x, HTTPException)]
                assert len(errors) == 1 and errors[0].detail['code'] == 'revision_conflict'
            snapshot = await client.call('read', doc)
            assert snapshot['revision'] == 1 and len(snapshot['outlines']['slides']) == 6

    asyncio.run(run())
