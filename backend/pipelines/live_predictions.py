"""Publish real, bounded-domain research inference. Never backfill missing inputs."""
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import h3
import h5py
import httpx
import numpy as np
import pandas as pd
from pipelines.extend_training import download
from pipelines.fire_history import history_features
from pipelines.rebuild_training import grid, in_study, source_url, THRESHOLD_MW
from pipelines.sources.goes import abi_scan_to_latlon
from pipelines.train import FEATURES, WEATHER, load_validated_model


def snapshot_path():
    return Path(os.getenv('FIRSTLIGHT_PREDICTIONS_PATH', 'lake/predictions/latest.json'))


def read_snapshot():
    try:
        data = json.loads(snapshot_path().read_text(encoding='utf-8'))
        now = pd.Timestamp.now(tz='UTC')
        generated, observed = (pd.Timestamp(data[key]) for key in ('generated_at', 'observed_at'))
        if any(t.tzinfo is None or pd.isna(t) or t > now for t in (generated, observed)):
            raise ValueError('Invalid snapshot time')
        if not isinstance(data['predictions'], list) or len(data['model_sha256']) != 64:
            raise ValueError('Invalid snapshot')
        for row in data['predictions']:
            if not h3.is_valid_cell(row['h3']) or h3.get_resolution(row['h3']) != 5 or not np.isfinite(row['probability']) or not 0 <= row['probability'] <= 1:
                raise ValueError('Invalid prediction')
        data['status'] = 'live' if now-generated <= pd.Timedelta(minutes=15) and now-observed <= pd.Timedelta(hours=3) else 'stale'
        return data
    except (OSError, ValueError, KeyError, TypeError):
        return {'status':'unavailable', 'predictions':[], 'model_sha256':None}


def scan(anchor, directory):
    name = download(anchor, directory)
    path = directory/name
    content = path.read_bytes()
    response = httpx.head(source_url(name), timeout=30)
    response.raise_for_status()
    if response.headers.get('etag', '').strip('"') != hashlib.md5(content).hexdigest():
        raise ValueError('NOAA checksum mismatch')
    published = pd.Timestamp(response.headers['last-modified'])
    digest = hashlib.sha256(content).hexdigest()
    rows = []
    with h5py.File(path, 'r') as dataset:
        stamp = dataset.attrs['time_coverage_start']
        observed = pd.Timestamp(stamp.decode() if isinstance(stamp, bytes) else stamp)
        if abs((observed-anchor).total_seconds()) > 60 or not observed <= published <= pd.Timestamp.now(tz='UTC'):
            raise ValueError('Invalid source timestamp')
        xs, ys, projection = grid(dataset)
        # Same pixels and quality gate, bounded memory on the free hosting tier.
        for start in range(0, len(ys), 128):
            power = dataset['Power'][start:start+128, :]
            quality = dataset['DQF'][start:start+128, :]
            yy, xx = np.where(np.isfinite(power) & (power > 0) & (quality == 0))
            for iy, ix in zip(yy, xx):
                lat, lon = abi_scan_to_latlon(float(xs[ix]), float(ys[start+iy]), **projection)
                if lat is not None and in_study(lat, lon):
                    rows.append(dict(h3=h3.latlng_to_cell(lat, lon, 5), frp=float(power[iy,ix]),
                                     raw_sha256=digest, published_at=published))
    return pd.DataFrame(rows, columns=['h3','frp','raw_sha256','published_at']), dict(
        observed_at=observed.isoformat(), published_at=published.isoformat(), sha256=digest, url=source_url(name))


def publish():
    model, report = load_validated_model(os.getenv('FIRSTLIGHT_MODEL_DIR', 'lake/models/frp-history-2024'))
    now = pd.Timestamp.now(tz='UTC')
    # Match the training cadence: one minute-zero scan every two hours.
    anchor = (now-pd.Timedelta(minutes=20)).floor('2h')
    directory = snapshot_path().parent/'raw'
    parts, sources = [], []
    for stamp in pd.date_range(anchor-pd.Timedelta(hours=24), anchor, freq='2h'):
        part, source = scan(stamp, directory)
        parts.append(part)
        sources.append(source)
    current = parts[-1]
    history = pd.concat([p for p in parts if not p.empty], ignore_index=True) if any(not p.empty for p in parts) else parts[-1]
    feature_time = pd.Timestamp(sources[-1]['published_at'])
    weather_time = feature_time.floor('h')-pd.Timedelta(days=1)
    predictions, withheld = [], 0
    with httpx.Client(timeout=30) as client:
        for cell, detections in current.groupby('h3'):
            if not all(in_study(*point) for point in h3.cell_to_boundary(cell)):
                continue
            lat, lon = h3.cell_to_latlng(cell)
            try:
                params = dict(latitude=lat, longitude=lon, start_date=str(weather_time.date()),
                              end_date=str(weather_time.date()), hourly=','.join(WEATHER), timezone='UTC', models='gfs_seamless')
                response = client.get('https://historical-forecast-api.open-meteo.com/v1/forecast', params=params)
                response.raise_for_status()
                hourly = pd.DataFrame(response.json()['hourly'])
                hourly.index = pd.to_datetime(hourly.time, utc=True)
                features = dict(detection_count=len(detections), mean_frp=float(detections.frp.mean()),
                                max_frp=float(detections.frp.max()), persistence=float(detections.frp.max() >= THRESHOLD_MW))
                features.update({key:float(hourly.at[weather_time,key]) for key in WEATHER})
                features.update(history_features(SimpleNamespace(h3=cell,feature_time=feature_time), history))
                values = pd.DataFrame([features])[FEATURES]
                if not np.isfinite(values.to_numpy()).all():
                    raise ValueError('Missing live feature')
                probability = float(model.predict(values)[0])
                if not np.isfinite(probability) or not 0 <= probability <= 1:
                    raise ValueError('Invalid model output')
                predictions.append(dict(h3=cell, probability=probability, features=features,
                    target_at=(pd.Timestamp(sources[-1]['observed_at'])+pd.Timedelta(days=1)).isoformat(),
                    weather_valid_at=weather_time.isoformat(), weather_retrieved_at=pd.Timestamp.now(tz='UTC').isoformat(),
                    weather_source=str(response.url), weather_sha256=hashlib.sha256(response.content).hexdigest(),
                    features_sha256=hashlib.sha256(values.to_json().encode()).hexdigest()))
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                withheld += 1
    if not current.empty and not predictions and withheld:
        raise ValueError('All candidate weather inputs unavailable; previous snapshot retained')
    data = dict(generated_at=pd.Timestamp.now(tz='UTC').isoformat(), observed_at=sources[-1]['observed_at'],
                feature_time=feature_time.isoformat(), model_sha256=report['model_sha256'],
                model_trained_at=report['trained_at'], metrics=report['metrics'], sources=sources,
                withheld_cells=withheld, predictions=sorted(predictions,key=lambda p:p['probability'],reverse=True),
                limitations='Research only: 2024 western-US active-fire model; current-year transfer unvalidated. Predicts a >=100 MW pixel in the same cell at the next-day scan, not ignition or spread. Weather lagged 24h; exact provider publication time unknown.')
    destination = snapshot_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, allow_nan=False), encoding='utf-8')
    temporary.replace(destination)
    print(f'Published {len(predictions)} real model scores; {withheld} cells withheld', flush=True)
    return data


if __name__ == '__main__':
    publish()
