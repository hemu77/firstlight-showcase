"""Rebuild a versioned, source-verified western-US 24-hour FRP dataset.

Twelve fixed scan anchors/day; active H3 R5 cells only. Labels refer to the
next-day scan, NOT any fire during the intervening day or physical fire absence.
Weather is historical forecast output lagged 24 hours (not ERA5 reanalysis).
"""
from __future__ import annotations
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import h3
import h5py
import httpx
import numpy as np
import pandas as pd
from pipelines.sources.goes import abi_scan_to_latlon, to_scalar_float
from pipelines.train import WEATHER

THRESHOLD_MW = 100.0
MIN_COVERAGE = .8
BOUNDS = (34, 48, -123, -108)
NAME = re.compile(r'OR_ABI-L2-FDCF-M6_G18_s(\d{4})(\d{3})(\d{2})(\d{2})\d+_e\d+_c\d+\.nc')


def in_study(lat, lon):
    south, north, west, east = BOUNDS
    return south <= lat <= north and west <= lon <= east


def source_url(name):
    match = NAME.fullmatch(name)
    if not match:
        raise ValueError('Not a NOAA GOES-18 FDCF object name')
    year, day, hour, _ = match.groups()
    return f'https://noaa-goes18.s3.amazonaws.com/ABI-L2-FDCF/{year}/{day}/{hour}/{name}'


def observed_label(power, quality):
    power, quality = np.asarray(power), np.asarray(quality)
    valid = ((quality == 0) & np.isfinite(power) & (power >= 0)) | (quality == 1)
    if len(valid) == 0 or valid.mean() < MIN_COVERAGE:
        return None
    return int(np.any((quality == 0) & np.isfinite(power) & (power >= THRESHOLD_MW)))


def grid(dataset):
    def axis(name):
        ds = dataset[name]
        return ds[:] * to_scalar_float(ds.attrs.get('scale_factor'), 1) + to_scalar_float(ds.attrs.get('add_offset'), 0)
    attrs = dataset['goes_imager_projection'].attrs
    return axis('x'), axis('y'), {
        'sub_lon_deg': to_scalar_float(attrs['longitude_of_projection_origin']),
        'h_sat': to_scalar_float(attrs['perspective_point_height']),
        'req': to_scalar_float(attrs['semi_major_axis']),
        'rpol': to_scalar_float(attrs['semi_minor_axis'])}


def cell_pixels(cell, xs, ys, projection):
    """Map all ABI pixel centres in a cell; cache only for an identical grid."""
    req, rpol = projection['req'], projection['rpol']
    H = req + projection['h_sat']
    def scan(lat, lon):
        phi = math.atan((rpol / req) ** 2 * math.tan(math.radians(lat)))
        radius = rpol / math.sqrt(1 - (1 - (rpol / req) ** 2) * math.cos(phi) ** 2)
        delta = math.radians(lon - projection['sub_lon_deg'])
        sx = H - radius * math.cos(phi) * math.cos(delta)
        sy = -radius * math.cos(phi) * math.sin(delta)
        sz = radius * math.sin(phi)
        return math.asin(-sy / math.sqrt(sx*sx + sy*sy + sz*sz)), math.atan2(sz, sx)
    boundary = [scan(*point) for point in h3.cell_to_boundary(cell)]
    xids = [int(np.argmin(abs(xs - x))) for x, _ in boundary]
    yids = [int(np.argmin(abs(ys - y))) for _, y in boundary]
    result = []
    for iy in range(max(0, min(yids)-2), min(len(ys), max(yids)+3)):
        for ix in range(max(0, min(xids)-2), min(len(xs), max(xids)+3)):
            lat, lon = abi_scan_to_latlon(float(xs[ix]), float(ys[iy]), **projection)
            if lat is not None and h3.latlng_to_cell(lat, lon, 5) == cell:
                result.append((iy, ix))
    return result


def rebuild(raw_dir, output, start='2026-08-07', end='2026-09-01', cutoff='2026-08-23'):
    start_time, end_time = pd.Timestamp(start, tz='UTC'), pd.Timestamp(end, tz='UTC')
    output.mkdir(parents=True, exist_ok=True)
    candidates = {}
    for path in sorted(raw_dir.glob('*.nc')):
        match = NAME.fullmatch(path.name)
        if not match:
            continue
        year, day, hour, minute = match.groups()
        if int(hour) % 2 or minute != '00':
            continue
        stamp = datetime.strptime(year+day+hour, '%Y%j%H').replace(tzinfo=timezone.utc)
        if start_time <= stamp < end_time:
            candidates[stamp] = path
    if not candidates:
        raise ValueError('No source scans in fixed study window')
    manifest, scans, rows = [], {}, []
    pixel_cache, grid_signature = {}, None
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        for index, (anchor, path) in enumerate(sorted(candidates.items())):
            content = path.read_bytes()
            url = source_url(path.name)
            response = client.head(url)
            response.raise_for_status()
            etag = response.headers.get('etag', '').strip('"')
            if etag != hashlib.md5(content).hexdigest():
                raise ValueError(f'Provider checksum mismatch: {path.name}')
            digest = hashlib.sha256(content).hexdigest()
            with h5py.File(path, 'r') as dataset:
                timestamp = dataset.attrs['time_coverage_start']
                if isinstance(timestamp, bytes):
                    timestamp = timestamp.decode()
                observed = pd.Timestamp(timestamp)
                if abs((observed.to_pydatetime() - anchor).total_seconds()) > 60:
                    raise ValueError('Filename and source timestamp disagree')
                xs, ys, projection = grid(dataset)
                signature = hashlib.sha256(xs.tobytes()+ys.tobytes()+json.dumps(projection, sort_keys=True).encode()).hexdigest()
                if grid_signature not in (None, signature):
                    raise ValueError('ABI grid changed; pixel cache cannot be reused')
                grid_signature = signature
                power = dataset['Power'][:]
                quality = dataset['DQF'][:]
                py, px = np.where(np.isfinite(power) & (power > 0) & (quality == 0))
                grouped = {}
                detections = []
                for iy, ix in zip(py, px):
                    lat, lon = abi_scan_to_latlon(float(xs[ix]), float(ys[iy]), **projection)
                    if lat is None or not in_study(lat, lon):
                        continue
                    cell = h3.latlng_to_cell(lat, lon, 5)
                    value = float(power[iy, ix])
                    grouped.setdefault(cell, []).append(value)
                    detections.append({'h3': cell, 'latitude': lat, 'longitude': lon, 'frp': value,
                                       'observed_at': observed, 'raw_sha256': digest, 'source_url': url})
                pd.DataFrame(detections, columns=['h3','latitude','longitude','frp','observed_at','raw_sha256','source_url']).to_parquet(output / f'detections-{anchor:%Y%m%d%H}.parquet', index=False)
                # Only currently active cells enter the cohort; future locations never select features.
                scans[anchor] = {'path': path, 'observed': observed, 'cells': grouped, 'sha256': digest}
                for cell, values in grouped.items():
                    if cell not in pixel_cache:
                        pixel_cache[cell] = cell_pixels(cell, xs, ys, projection)
                manifest.append({'url': url, 'sha256': digest, 'etag': etag, 'verified_at': datetime.now(timezone.utc).isoformat(), 'observed_at': observed.isoformat(), 'detections': len(detections)})
            if index % 10 == 0:
                print(f'Verified/rebuilt {index+1}/{len(candidates)} scans', flush=True)
        censored = 0
        for anchor, scan in sorted(scans.items()):
            following = scans.get(anchor + timedelta(days=1))
            if following is None:
                continue
            with h5py.File(following['path'], 'r') as dataset:
                power, quality = dataset['Power'][:], dataset['DQF'][:]
                for cell, values in scan['cells'].items():
                    pixels = pixel_cache[cell]
                    yy, xx = zip(*pixels) if pixels else ([], [])
                    label = observed_label(power[yy, xx], quality[yy, xx])
                    if label is None:
                        censored += 1
                        continue
                    lat, lon = h3.cell_to_latlng(cell)
                    rows.append({'h3': cell, 'feature_time': scan['observed'], 'label_end': following['observed'] + pd.Timedelta(minutes=10),
                                 'region': 'east_holdout' if lon >= -115 else 'west_train', 'target': label, 'provenance': 'observed',
                                 'detection_count': len(values), 'mean_frp': float(np.mean(values)), 'max_frp': max(values),
                                 'persistence': float(max(values) >= THRESHOLD_MW), 'raw_sha256': scan['sha256'], 'label_raw_sha256': following['sha256']})
        frame = pd.DataFrame(rows)
        if frame.empty:
            raise ValueError('No uncensored outcomes')
        weather_manifest = []
        for index, cell in enumerate(sorted(frame.h3.unique())):
            lat, lon = h3.cell_to_latlng(cell)
            cache = output / f'weather-{cell}.json'
            params = {'latitude':lat, 'longitude':lon, 'start_date':str((start_time-pd.Timedelta(days=1)).date()), 'end_date':str((end_time-pd.Timedelta(days=1)).date()), 'hourly':','.join(WEATHER), 'timezone':'UTC', 'models':'gfs_seamless'}
            url = 'https://historical-forecast-api.open-meteo.com/v1/forecast'
            if not cache.exists():
                response = client.get(url, params=params)
                response.raise_for_status()
                cache.write_bytes(response.content)
            data = json.loads(cache.read_text())
            hourly = pd.DataFrame(data['hourly'])
            hourly.index = pd.to_datetime(hourly.time, utc=True)
            for rowid in frame.index[frame.h3 == cell]:
                valid_time = frame.at[rowid, 'feature_time'].floor('h') - pd.Timedelta(days=1)
                for variable in WEATHER:
                    frame.at[rowid, variable] = hourly.at[valid_time, variable]
                frame.at[rowid, 'weather_valid_time'] = valid_time
            weather_manifest.append({'h3': cell, 'url': url, 'params': params, 'sha256': hashlib.sha256(cache.read_bytes()).hexdigest()})
            if index % 10 == 0:
                print(f'Weather joined {index+1}/{frame.h3.nunique()} cells', flush=True)
        features = output / 'features.parquet'
        frame.to_parquet(features, index=False)
        report = {'training_approved': False, 'input_sha256':hashlib.sha256(features.read_bytes()).hexdigest(),
                  'rows':len(frame), 'positive_rows':int(frame.target.sum()), 'censored_cloud_or_invalid':censored,
                  'sources':manifest, 'weather':weather_manifest, 'definition':__doc__, 'threshold_mw':THRESHOLD_MW,
                  'minimum_coverage':MIN_COVERAGE, 'cutoff':cutoff, 'holdout_region':'east_holdout',
                  'limitations':[f'{(end_time-start_time).days}-day study; no seasonal generalization established.', 'Weather forecast valid times lag features by 24h; exact historical API publication timestamps are not available.', '80% valid-pixel coverage; residual obscuration remains.', 'No fire-ignition, spread or emergency prediction.']}
        (output / 'audit.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps({k:v for k,v in report.items() if k not in ('sources','weather')}, indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw-dir', type=Path, default=Path('lake/raw/noaa-goes18-fdcf'))
    parser.add_argument('--output', type=Path, default=Path('lake/training-v2'))
    parser.add_argument('--start', default='2026-08-07')
    parser.add_argument('--end', default='2026-09-01')
    parser.add_argument('--cutoff', default='2026-08-23')
    args = parser.parse_args()
    rebuild(args.raw_dir, args.output, args.start, args.end, args.cutoff)
