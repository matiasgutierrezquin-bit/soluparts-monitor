"""Un control de navegador. Sin servicios de pago ni envío de correos."""
import argparse
import asyncio
import datetime as dt
import hashlib
import json
import os
import re
import tempfile
import time
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

from .core import UTC, iso, parse
from .probe import probe
from .inventory import inventory_value, coverage

ROOT = Path(__file__).resolve().parent.parent
GOOD = {'ok', 'slow', 'transient'}
STATUSES = ('monitor_error', 'blocked', 'http_error', 'network_error', 'product_error',
            'inconclusive', 'transient', 'slow', 'ok')


def operational_observation(observations):
    if any(item.get('status') in {'monitor_error', 'blocked', 'inconclusive'} for item in observations):
        return False
    for kind in ('home', 'search'):
        if not any(item.get('kind') == kind and item.get('status') in GOOD for item in observations):
            return False
    products = [item for item in observations if item.get('kind') == 'product']
    return len({item['url'] for item in products if item.get('status') in GOOD}) >= 3


def overall(observations):
    return next((s for s in STATUSES if any(item.get('status') == s for item in observations)), 'monitor_error')


def compact(item):
    """Bound diagnostics while preserving measured main outcomes and retry evidence."""
    result = {}
    basic = ('url', 'kind', 'at', 'status', 'http_status', 'dom_ms', 'render_ms', 'elapsed_ms',
             'cf_ray', 'title', 'final_url', 'reason', 'product_links_count', 'load_event_timeout',
             'screenshot_sha256', 'html_sha256')
    for key in basic:
        if key in item:
            value = item[key]
            result[key] = value[:1500] if isinstance(value, str) else value
    for key in ('network_failures', 'http_resource_errors', 'script_errors'):
        source = item.get(key, [])
        result[key + '_count'] = len(source)
        result[key] = [str(x)[:500] if isinstance(x, str) else
                       {k: str(v)[:500] if isinstance(v, str) else v for k, v in x.items()}
                       for x in source[:5]]
    if 'product' in item:
        product = item['product']
        result['product'] = {'name': str(product.get('name', ''))[:400],
                             'prices': [str(x)[:100] for x in product.get('prices', [])[:8]],
                             'images': [{k: str(v)[:700] if isinstance(v, str) else v for k,v in x.items()}
                                        for x in product.get('images', [])[:3]]}
    if 'first_attempt' in item:
        result['first_attempt'] = compact(item['first_attempt'])
    result['html_retained'] = False
    return result


def save_cycle(data_dir, config, observations, products, observed_at, duration_seconds, run_id, state):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', run_id):
        raise ValueError('ID inválido')
    record_rel = Path('records') / observed_at.date().isoformat() / (run_id + '.json')
    record_path = data_dir / record_rel
    if record_path.exists():
        raise ValueError('Ya existe ese control; no se sobrescribe evidencia')
    record_path.parent.mkdir(parents=True, exist_ok=True)
    records = [compact(x) for x in observations]
    evidence = []
    # Small, documented sample. All measured outcomes remain in the JSON.
    candidates = [x for item in observations for x in (item.get('first_attempt'), item) if x]
    for item in candidates:
        filename = item.get('screenshot')
        if not filename:
            continue
        file = Path(filename)
        if not file.is_file() or file.stat().st_size > 64 * 1024:
            continue
        destination = Path('evidence') / (run_id + '.jpg')
        (data_dir / destination).parent.mkdir(parents=True, exist_ok=True)
        content = file.read_bytes()
        (data_dir / destination).write_bytes(content)
        evidence.append({'path': destination.as_posix(), 'sha256': hashlib.sha256(content).hexdigest()})
        break
    readiness = operational_observation(observations)
    result = {'schema': 1, 'id': run_id, 'observed_at_utc': iso(observed_at),
              'observed_at_chile': observed_at.astimezone(ZoneInfo('America/Santiago')).isoformat(),
              'duration_seconds': round(duration_seconds, 3), 'status': overall(observations),
              'operational_observation': readiness, 'observations': records, 'evidence': evidence,
              'catalog_coverage':coverage(inventory_value(products)) | {
                  'products_checked_this_cycle':sum(x.get('kind')=='product' for x in observations),
                  'products_loaded_this_cycle':sum(x.get('kind')=='product' and x.get('status') in GOOD for x in observations)},
              'diagnostic_policy': 'First 5 network/resource/JS diagnostics per page; at most one screenshot <=64KiB; no full HTML',
              'schedule_target_minutes': 20, 'exact_schedule_guaranteed': False}
    raw = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2).encode()
    if len(raw) > 128 * 1024:
        raise ValueError('Registro demasiado grande; no se trunca silenciosamente')
    record_path.write_bytes(raw)
    summary = {'id': run_id, 'at': iso(observed_at), 'status': result['status'],
               'operational_observation': readiness, 'record': record_rel.as_posix(),
               'sha256': hashlib.sha256(raw).hexdigest()}
    with (data_dir / 'history.ndjson').open('a', encoding='utf-8') as target:
        target.write(json.dumps(summary, ensure_ascii=False) + '\n')
    inventory=inventory_value(products)
    (data_dir / 'inventory.json').write_text(json.dumps(inventory,ensure_ascii=False,separators=(',',':')))
    state.update({'last_observation': summary, 'catalog_coverage': coverage(inventory),
                  'calibrated': config.get('_calibrated', {}), 'stop_at': config['stop_at']})
    if readiness:
        state['last_operational_observation'] = summary
    (data_dir / 'state.json').write_text(json.dumps(state, ensure_ascii=False, indent=2))
    return result


async def run(data_dir, config, now=None, run_id=None):
    now = now or dt.datetime.now(UTC)
    if now >= parse(config['stop_at']):
        return {'skipped': True, 'reason': 'campaign_finished'}
    data_dir.mkdir(parents=True, exist_ok=True)
    state_file = data_dir / 'state.json'
    state = json.loads(state_file.read_text()) if state_file.exists() else {}
    config['_calibrated'] = state.get('calibrated', {})
    inventory_file=data_dir/'inventory.json'
    inventory=json.loads(inventory_file.read_text()) if inventory_file.exists() else inventory_value(state.get('products',[]))
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='soluparts-public-check-') as scratch:
        try:
            slot = int(now.timestamp()) // (20 * 60)
            observations, products = await asyncio.wait_for(
                probe(config, Path(scratch), slot, inventory), timeout=540)
        except Exception as error:
            observations = [{'kind': 'monitor', 'url': config['site'], 'at': iso(now),
                             'status': 'monitor_error', 'reason': type(error).__name__ + ': ' + str(error)[:1000]}]
            products = inventory
        return save_cycle(data_dir, config, observations, products, now,
                          time.monotonic() - started, run_id or uuid.uuid4().hex, state)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', required=True)
    args = parser.parse_args()
    config = json.loads((ROOT / 'config.json').read_text())
    run_id = os.getenv('GITHUB_RUN_ID', uuid.uuid4().hex) + '-' + os.getenv('GITHUB_RUN_ATTEMPT', '1')
    result = asyncio.run(run(Path(args.data_dir), config, run_id=run_id))
    print(json.dumps({'saved': not result.get('skipped', False), 'status': result.get('status'),
                      'operational_observation': result.get('operational_observation', False)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
