"""Small private GitHub snapshot store. Credentials are server environment only."""
import base64
from datetime import datetime, timezone, timedelta
import json
import math
import os
import re
import h3
import httpx

REPO = 'hemu77/firstlight-predictions'


def timestamp(value):
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('Timezone required')
    return stamp


def validate_snapshot(data):
    now = datetime.now(timezone.utc)
    try:
        generated, observed = (timestamp(data[k]) for k in ('generated_at', 'observed_at'))
        if max(generated, observed) > now or not re.fullmatch('[a-f0-9]{64}', data['model_sha256']):
            raise ValueError('Invalid provenance')
        if not isinstance(data['predictions'], list) or len(data['predictions']) > 10000:
            raise ValueError('Invalid prediction list')
        for p in data['predictions']:
            if not h3.is_valid_cell(p['h3']) or h3.get_resolution(p['h3']) != 5 or isinstance(p['probability'], bool) or not math.isfinite(p['probability']) or not 0 <= p['probability'] <= 1:
                raise ValueError('Invalid score')
            timestamp(p['target_at'])
            timestamp(p['weather_valid_at'])
            if not all(math.isfinite(p['features'][k]) for k in ('max_frp','history_max_frp','relative_humidity_2m','wind_speed_10m')):
                raise ValueError('Invalid features')
        return {**data, 'status': 'live' if now-generated <= timedelta(minutes=15) and now-observed <= timedelta(hours=3) else 'stale'}
    except (KeyError, TypeError, AttributeError, OverflowError) as exc:
        raise ValueError('Invalid snapshot') from exc


def should_refresh(data):
    now = datetime.now(timezone.utc)-timedelta(minutes=20)
    anchor = now.replace(hour=(now.hour//2)*2, minute=0, second=0, microsecond=0)
    return data is None or timestamp(data['observed_at']) < anchor


def client():
    token = os.environ['FIRSTLIGHT_GITHUB_TOKEN']
    return httpx.Client(base_url=f'https://api.github.com/repos/{REPO}/contents/',
        headers={'Authorization': f'Bearer {token}', 'Accept':'application/vnd.github+json',
                 'X-GitHub-Api-Version':'2022-11-28'}, timeout=30)


def read_file(path):
    with client() as api:
        response = api.get(path)
        response.raise_for_status()
        return base64.b64decode(response.json()['content'], validate=False)


def read_remote():
    return validate_snapshot(json.loads(read_file('latest-predictions.json')))


def write_remote(data):
    validate_snapshot(data)
    encoded = base64.b64encode(json.dumps(data, allow_nan=False).encode()).decode()
    if len(encoded) > 1_000_000:
        raise ValueError('Snapshot exceeds portfolio storage limit')
    with client() as api:
        response = api.get('latest-predictions.json')
        payload = {'message':'Update verified prediction snapshot', 'content':encoded}
        if response.status_code != 404:
            response.raise_for_status()
            item = response.json()
            old = validate_snapshot(json.loads(base64.b64decode(item['content'])))
            if timestamp(old['generated_at']) >= timestamp(data['generated_at']):
                return
            payload['sha'] = item['sha']
        # GitHub's SHA check rejects competing writes; never force-overwrite.
        result = api.put('latest-predictions.json', json=payload)
        result.raise_for_status()

