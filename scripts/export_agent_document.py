"""Export an exact manuscript from the same Presenton instance as the MCP client."""

import argparse
import base64
import copy
from datetime import datetime
import json
import mimetypes
import os
from pathlib import Path
import shutil
import subprocess
from time import perf_counter
from urllib.parse import unquote, urlsplit
import uuid
import zipfile

import httpx


ROOT = Path(__file__).resolve().parents[1]


def connection(config_path=None):
    config_root = os.environ.get('CLAUDE_CONFIG_DIR')
    default = Path(config_root) / '.claude.json' if config_root else Path.home() / '.claude.json'
    path = Path(config_path) if config_path else default
    entry = json.loads(path.read_text(encoding='utf-8'))['mcpServers']['presenton']
    parsed = urlsplit(entry['url'])
    mcp_path = parsed.path.rstrip('/')
    suffix = next((value for value in ('/mcp/legacy', '/mcp/workflow', '/mcp')
                   if mcp_path.endswith(value)), None)
    if (entry.get('type') != 'http' or parsed.scheme not in {'http', 'https'}
            or not parsed.netloc or parsed.username or parsed.password
            or parsed.query or parsed.fragment or suffix is None):
        raise ValueError('Expected a Presenton HTTP MCP URL ending in /mcp, /mcp/workflow or /mcp/legacy.')
    base = parsed._replace(path=mcp_path[:-len(suffix)]).geturl()
    return base, entry.get('headers', {})


def require_ok(response, operation):
    if response.is_error or response.is_redirect:
        # Error bodies may include credentials or document content. Never print them.
        raise ValueError(
            f'{operation}: HTTP {response.status_code}. Check the selected MCP instance '
            'and credentials; do not substitute another document ID or recreate the deck.'
        )
    return response


def read_document(client, document_id, expected_revision=None):
    opened = require_ok(client.post('/api/v1/agent-tools/sessions',
        json={'label': 'Local snapshot export'}), 'Open export session').json()
    response = require_ok(client.get(f'/api/v1/agent-tools/documents/{document_id}',
        headers={'X-Presenton-Session': opened['sessionToken']}), 'Read requested document')
    document = response.json()
    if uuid.UUID(document['documentId']) != uuid.UUID(str(document_id)):
        raise ValueError('Returned document ID differs from the requested ID; export stopped.')
    if expected_revision is not None and document['revision'] != expected_revision:
        raise ValueError('Document revision changed; read the current manuscript before exporting.')
    if document['phase'] != 'ready' or not document['slides']:
        raise ValueError('Complete the existing document through MCP before exporting.')
    return document


def render_snapshot(client, base_url, document):
    """Resolve authorized assets on the same server, never from a demo data directory."""
    origin = urlsplit(base_url)
    cache = {}

    def asset(reference):
        if reference.startswith('data:'):
            return reference
        parsed = urlsplit(reference)
        if parsed.scheme or parsed.netloc:
            if (parsed.scheme, parsed.netloc) != (origin.scheme, origin.netloc):
                raise ValueError('Cross-origin asset rejected; no credentials were forwarded.')
        path = unquote(parsed.path)
        if (parsed.query or parsed.fragment or '\\' in path
                or '..' in path.split('/') or not path.startswith(('/app_data/', '/static/', '/vendor/'))):
            raise ValueError('Unsupported asset reference; export stopped.')
        if reference not in cache:
            response = require_ok(client.get(parsed.path), 'Fetch manuscript asset')
            if len(response.content) > 32 * 1024 * 1024:
                raise ValueError('Asset exceeds local export size limit.')
            mime = response.headers.get('content-type', '').split(';')[0]
            if mime in {'', 'application/octet-stream'}:
                mime = mimetypes.guess_type(path)[0] or 'application/octet-stream'
            if mime == 'text/html':
                raise ValueError('Asset endpoint returned HTML; check the MCP instance.')
            cache[reference] = f'data:{mime};base64,' + base64.b64encode(response.content).decode()
        return cache[reference]

    def walk(value):
        if isinstance(value, list):
            return [walk(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {key: walk(item) for key, item in value.items()}
        for key in ('url', 'image_url', 'icon_url', '__image_url__', '__icon_url__'):
            if isinstance(result.get(key), str):
                result[key] = asset(result[key])
        if result.get('type') == 'image' and isinstance(result.get('data'), str):
            result['data'] = asset(result['data'])
        return result

    snapshot = copy.deepcopy(document)
    for slide in snapshot['slides']:
        slide['ui'] = walk(slide['ui'])
    snapshot['theme'] = walk(snapshot.get('theme'))
    return snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--document-id', type=uuid.UUID)
    parser.add_argument('--expected-revision', type=int)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--mcp-config', type=Path)
    parser.add_argument('--check', action='store_true', help='Check the connection before creating a deck.')
    args = parser.parse_args()
    if not args.check and args.document_id is None:
        parser.error('--document-id is required for export')
    base, headers = connection(args.mcp_config)
    started = perf_counter()
    with httpx.Client(base_url=base, headers=headers, timeout=30, trust_env=False) as client:
        caps = require_ok(client.get('/api/v1/agent-tools/capabilities'), 'Check external tools').json()
        if caps.get('capabilities', {}).get('internalAgent') is not False:
            raise ValueError('The selected server is not in external Agent mode.')
        if args.check:
            print(json.dumps({'source': base, 'externalAgent': True, 'ready': True}))
            return
        document = read_document(client, args.document_id, args.expected_revision)
        rendered = render_snapshot(client, base, document)
    read_seconds = perf_counter() - started
    node = shutil.which('node')
    if not node:
        raise ValueError('Node.js is required for the existing Presenton exporter.')
    output = (args.output_dir or ROOT / 'out' /
        f"presentation-{args.document_id}-r{document['revision']}-{datetime.now():%Y%m%d-%H%M%S-%f}").resolve()
    output.mkdir(parents=True, exist_ok=False)
    for filename, value in [('snapshot.json', document), ('render-snapshot.json', rendered)]:
        (output / filename).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    subprocess.run([node, str(ROOT / 'scripts/export-agent-document.mjs'), str(output)], cwd=ROOT, check=True)
    deck = output / 'presentation.pptx'
    with zipfile.ZipFile(deck) as archive:
        slides = [p for p in archive.namelist() if p.startswith('ppt/slides/slide') and p.endswith('.xml')]
    if len(slides) != len(document['slides']):
        raise ValueError('Exported page count does not match the document snapshot.')
    manifest = {'documentId': str(args.document_id), 'revision': document['revision'],
        'source': base, 'slideCount': len(slides), 'pptx': str(deck),
        'readAndAssetsSeconds': round(read_seconds, 3),
        'totalSeconds': round(perf_counter() - started, 3),
        'mode': 'local snapshot export using native export-core',
        'limitations': ['Not a durable export job; tables may be rasterized and fonts may fall back.']}
    (output / 'export-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, OSError, httpx.HTTPError) as exc:
        # Do not echo transport/config exception text, which may contain a private URL.
        raise SystemExit(str(exc) if type(exc) is ValueError else f'Export stopped: {type(exc).__name__}. Check configuration and connection.') from None
