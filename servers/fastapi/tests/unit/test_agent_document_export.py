import importlib.util
import json
from pathlib import Path
import uuid

import httpx
import pytest


spec = importlib.util.spec_from_file_location(
    'export_agent_document', Path(__file__).resolve().parents[4] / 'scripts/export_agent_document.py'
)
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


@pytest.mark.parametrize('endpoint', ['/mcp', '/mcp/', '/mcp/legacy', '/mcp/legacy/', '/mcp/workflow'])
@pytest.mark.parametrize('prefix', ['', '/presenton'])
def test_connection_uses_selected_mcp_instance(tmp_path, endpoint, prefix):
    path = tmp_path / 'client.json'
    path.write_text(json.dumps({'mcpServers': {'presenton': {
        'type': 'http', 'url': 'http://127.0.0.1:5002' + prefix + endpoint,
        'headers': {'Authorization': 'Bearer fixture'},
    }}}))
    base, headers = exporter.connection(path)
    assert base == 'http://127.0.0.1:5002' + prefix
    assert headers == {'Authorization': 'Bearer fixture'}


@pytest.mark.parametrize('endpoint', ['/mcp/unrecognized', '/mcp/legacy?key=private', '/mcp/workflow#fragment'])
def test_connection_rejects_unknown_profile_and_embedded_credentials(tmp_path, endpoint):
    path = tmp_path / 'client.json'
    path.write_text(json.dumps({'mcpServers': {'presenton': {
        'type': 'http', 'url': 'http://127.0.0.1:5002' + endpoint,
    }}}))
    with pytest.raises(ValueError, match='Expected a Presenton HTTP MCP URL'):
        exporter.connection(path)


def test_document_404_stops_without_fallback_or_recreation():
    calls = []

    def handle(request):
        calls.append((request.method, str(request.url)))
        if request.url.path.endswith('/sessions'):
            return httpx.Response(200, json={'sessionToken': 'private-session'})
        assert request.headers['X-Presenton-Session'] == 'private-session'
        return httpx.Response(404, json={'private-detail': 'must-not-be-logged'})

    with httpx.Client(base_url='http://127.0.0.1:5002', transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ValueError, match='HTTP 404') as caught:
            exporter.read_document(client, uuid.uuid4())
    assert 'must-not-be-logged' not in str(caught.value)
    assert len(calls) == 2
    assert calls[0][0] == 'POST' and calls[1][0] == 'GET'
    assert all(':5002/' in url for _, url in calls)


@pytest.mark.parametrize('mismatch', ['id', 'revision'])
def test_snapshot_identity_and_revision_are_checked(mismatch):
    requested = uuid.uuid4()

    def handle(request):
        if request.url.path.endswith('/sessions'):
            return httpx.Response(200, json={'sessionToken': 'fixture'})
        return httpx.Response(200, json={
            'documentId': str(uuid.uuid4() if mismatch == 'id' else requested),
            'revision': 16, 'phase': 'ready', 'slides': [{}],
        })

    with httpx.Client(base_url='http://test', transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ValueError, match='differs|revision changed'):
            exporter.read_document(client, requested, expected_revision=15)


def test_assets_use_same_authenticated_instance_and_keep_raw_snapshot():
    seen = []

    def handle(request):
        seen.append(request)
        assert request.url.host == '127.0.0.1' and request.url.port == 5002
        assert request.headers['Authorization'] == 'Bearer fixture'
        return httpx.Response(200, content=b'<svg/>', headers={'content-type': 'image/svg+xml'})

    document = {'slides': [{'ui': [
        {'type': 'image', 'data': '/app_data/templates/modern/static/icon.svg'},
        {'type': 'image', 'data': '/app_data/templates/modern/static/icon.svg'},
    ]}], 'theme': None}
    with httpx.Client(base_url='http://127.0.0.1:5002', headers={'Authorization': 'Bearer fixture'},
                      transport=httpx.MockTransport(handle)) as client:
        rendered = exporter.render_snapshot(client, 'http://127.0.0.1:5002', document)
    assert len(seen) == 1
    assert rendered['slides'][0]['ui'][0]['data'].startswith('data:image/svg+xml;base64,')
    assert document['slides'][0]['ui'][0]['data'].startswith('/app_data/')


@pytest.mark.parametrize('reference', [
    'https://elsewhere.example/image.svg', '//elsewhere.example/image.svg',
    '/app_data/%2e%2e/config.json', '/unrelated/file',
])
def test_assets_do_not_forward_credentials_or_read_unrelated_paths(reference):
    def handle(request):
        pytest.fail('Forbidden asset must be rejected before any HTTP request')

    with httpx.Client(base_url='http://test', transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(ValueError):
            exporter.render_snapshot(client, 'http://test', {
                'slides': [{'ui': {'type': 'image', 'data': reference}}], 'theme': None,
            })
