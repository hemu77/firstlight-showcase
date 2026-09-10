"""Strictly past, publication-time-aware fire history; no absence-of-fire labels."""
import hashlib
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import h3
import httpx
import numpy as np
import pandas as pd
from pipelines.train import FEATURES


def history_features(row, detections):
    if detections.published_at.isna().any():
        raise ValueError('Missing history publication timestamp')
    now = pd.Timestamp(row.feature_time)
    past = detections[(detections.published_at < now) & (detections.published_at >= now-pd.Timedelta(hours=24))]
    local = past[past.h3 == row.h3]
    neighbors = past[past.h3.isin(h3.grid_disk(row.h3, 1))]
    phase = 2*np.pi*(now.hour+now.minute/60)/24
    return dict(history_scan_count=int(local.raw_sha256.nunique()),
                history_max_frp=float(local.frp.max()) if len(local) else 0.,
                history_mean_frp=float(local.frp.mean()) if len(local) else 0.,
                history_age_hours=float((now-local.published_at.max()).total_seconds()/3600) if len(local) else 25.,
                neighbor_max_frp=float(neighbors.frp.max()) if len(neighbors) else 0.,
                hour_sin=float(np.sin(phase)), hour_cos=float(np.cos(phase)))


def build(directories=None, output=Path('lake/training-v3'), cutoff='2026-09-01'):
    directories = directories or [Path('lake/training-v2'), Path('lake/training-september')]
    output.mkdir(parents=True, exist_ok=True)
    frames, records, detections, publication = [], {}, [], {}
    for directory in directories:
        audit = json.loads((directory/'approved_audit.json').read_text())
        path = directory/'audited_features.parquet'
        if audit['training_approved'] is not True or hashlib.sha256(path.read_bytes()).hexdigest() != audit['input_sha256']:
            raise ValueError('Base feature audit mismatch')
        frames.append(pd.read_parquet(path))
        records.update({record['sha256']:record for record in audit['sources']})
        publication.update(audit.get('source_publication_times', {}))
        detections.extend(pd.read_parquet(path) for path in sorted(directory.glob('detections-*.parquet')))
    cache = output/'publication.json'
    if cache.exists():
        publication.update(json.loads(cache.read_text()))
    def published(record):
        response = httpx.head(record['url'], timeout=30)
        response.raise_for_status()
        if response.headers['etag'].strip('"') != record['etag']:
            raise ValueError('Source revised since audit')
        return record['sha256'], pd.Timestamp(response.headers['last-modified']).isoformat()
    with ThreadPoolExecutor(max_workers=4) as pool:
        publication.update(pool.map(published, [record for key,record in records.items() if key not in publication]))
    cache.write_text(json.dumps(publication), encoding='utf-8')
    history = pd.concat([part for part in detections if not part.empty], ignore_index=True).drop_duplicates(['raw_sha256','latitude','longitude'])
    if not set(history.raw_sha256) <= set(records):
        raise ValueError('Unaudited detection source')
    history['published_at'] = pd.to_datetime(history.raw_sha256.map(publication), utc=True)
    frame = pd.concat(frames, ignore_index=True).drop_duplicates(['h3','raw_sha256'])
    for index,row in frame.iterrows():
        for name,value in history_features(row, history).items():
            frame.at[index,name] = value
    if not np.isfinite(frame[FEATURES].to_numpy()).all():
        raise ValueError('Invalid history features')
    destination = output/'audited_features.parquet'
    frame.to_parquet(destination, index=False)
    report = {'training_approved':True, 'input_sha256':hashlib.sha256(destination.read_bytes()).hexdigest(),
              'history_artifact_hashes':{str(path):hashlib.sha256(path.read_bytes()).hexdigest() for directory in directories for path in sorted(directory.glob('detections-*.parquet'))},
              'rows':len(frame), 'cutoff':cutoff, 'holdout_region':'east_holdout',
              'parent_audits':{str(d):hashlib.sha256((d/'approved_audit.json').read_bytes()).hexdigest() for d in directories},
              'definition':'24-hour high-FRP scan classification in currently active western-US H3 R5 cells; source-published history from preceding 24h.',
              'approval_scope':f'Retrospective research; predeclared temporal/geographic holdouts starting {cutoff}.',
              'limitations':['Short summer study; correlated cells and times.', 'Historical GFS weather is lagged 24h; exact API publication times unavailable.', 'Censored outcomes require 80% valid ABI pixel centres; not emergency guidance.']}
    (output/'approved_audit.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(f'Built {len(frame)} audited history-enriched examples', flush=True)


if __name__ == '__main__':
    build()
