"""NOAA GOES ABI-L2-FDCF and GLM-L2-LCFA adapter with live S3 discovery, NetCDF extraction & exact ABI geostationary projection."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import io
import hashlib
import json
import math
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET
import h5py
import httpx
import numpy as np
from pipelines.sources.envelope import ObservationEnvelope

GOES18_S3_BASE = "https://noaa-goes18.s3.amazonaws.com"
GOES19_S3_BASE = "https://noaa-goes19.s3.amazonaws.com"


def to_scalar_float(val: Any, default: float = 0.0) -> float:
    """Converts scalar or 1-element array HDF5 attribute to native float."""
    if val is None:
        return default
    try:
        return float(np.asarray(val).item())
    except (ValueError, TypeError):
        return default


def abi_scan_to_latlon(
    x_rad: float,
    y_rad: float,
    sub_lon_deg: float = -137.0,
    h_sat: float = 35786023.0,
    req: float = 6378137.0,
    rpol: float = 6356752.31414
) -> tuple[float | None, float | None]:
    """Converts GOES-R ABI fixed grid scan angles (x, y in radians) to geodetic latitude/longitude.
    
    Implements NOAA Product Definition and User's Guide (PUG) Volume 5 geostationary coordinate transform.
    Returns (None, None) if the ray misses the Earth ellipsoid (off-disk).
    """
    H = h_sat + req
    lam0 = math.radians(sub_lon_deg)
    
    cos_x = math.cos(x_rad)
    sin_x = math.sin(x_rad)
    cos_y = math.cos(y_rad)
    sin_y = math.sin(y_rad)
    
    e_ratio_sq = (req / rpol) ** 2
    
    a = sin_x**2 + cos_x**2 * (cos_y**2 + e_ratio_sq * sin_y**2)
    b = -2.0 * H * cos_x * cos_y
    c = H**2 - req**2
    
    disc = b**2 - 4.0 * a * c
    if disc < 0:
        return None, None  # Ray misses Earth disk
        
    r_s = (-b - math.sqrt(disc)) / (2.0 * a)
    s_x = r_s * cos_x * cos_y
    s_y = -r_s * sin_x
    s_z = r_s * cos_x * sin_y
    
    denom = math.sqrt((H - s_x)**2 + s_y**2)
    if denom == 0:
        return None, None
        
    lat_rad = math.atan(e_ratio_sq * (s_z / denom))
    lon_rad = lam0 - math.atan(s_y / (H - s_x))
    
    lat_deg = math.degrees(lat_rad)
    lon_deg = (math.degrees(lon_rad) + 180.0) % 360.0 - 180.0
    
    if not (-90.0 <= lat_deg <= 90.0 and -180.0 <= lon_deg <= 180.0):
        return None, None
        
    return round(lat_deg, 5), round(lon_deg, 5)


def list_s3_anonymous_keys(bucket_url: str = GOES18_S3_BASE, prefix: str = "ABI-L2-FDCF/", max_keys: int = 10) -> list[str]:
    """Lists S3 bucket keys via anonymous HTTPS XML listing without credentials."""
    try:
        resp = httpx.get(f"{bucket_url}/?prefix={prefix}&max-keys={max_keys}", timeout=10.0)
        if resp.status_code != 200:
            return []
        root = ET.fromstring(resp.text)
        namespace = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
        keys = [elem.text for elem in root.findall(".//s3:Key", namespace) if elem.text and elem.text.endswith(".nc")]
        return keys
    except (httpx.RequestError, httpx.HTTPStatusError, ET.ParseError) as e:
        print(f"[GOES S3] Listing network/XML warning for {prefix}: {e}")
        return []
    except Exception as e:
        print(f"[GOES S3] Unexpected listing error for {prefix}: {type(e).__name__}: {e}")
        return []


def discover_latest_goes_s3_key(
    product: str = "ABI-L2-FDCF",
    bucket_url: str = GOES18_S3_BASE,
    max_search_hours: int = 36,
    target_dt: datetime | None = None
) -> str | None:
    """Discovers the most recent available NetCDF object key on NOAA S3 by searching recent hourly partitions."""
    start_dt = target_dt or datetime.now(timezone.utc)
    for h in range(max_search_hours):
        dt = start_dt - timedelta(hours=h)
        date_prefix = dt.strftime("%Y/%j/%H")
        prefix = f"{product}/{date_prefix}/"
        keys = list_s3_anonymous_keys(bucket_url, prefix, max_keys=10)
        if keys:
            return sorted(keys)[-1]
    return None


def download_goes_netcdf(key: str, bucket_url: str = GOES18_S3_BASE, dest_dir: str | Path = "lake/raw/noaa-goes18") -> bytes | None:
    """Downloads real NetCDF payload from NOAA S3 and stores raw bytes in lake/raw/."""
    try:
        url = f"{bucket_url}/{key}"
        resp = httpx.get(url, timeout=30.0)
        if resp.status_code == 200:
            filename = Path(key).name
            out_path = Path(dest_dir) / filename
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(resp.content)
            return resp.content
    except (httpx.RequestError, httpx.HTTPStatusError, OSError) as e:
        print(f"[GOES S3] Download I/O warning for {key}: {e}")
    except Exception as e:
        print(f"[GOES S3] Unexpected download error for {key}: {type(e).__name__}: {e}")
    return None


def extract_glm_from_netcdf_bytes(nc_bytes: bytes, satellite: str = "noaa-goes18") -> list[ObservationEnvelope]:
    """Extracts lightning flash observations from real NetCDF HDF5 payload."""
    envelopes = []
    try:
        f = h5py.File(io.BytesIO(nc_bytes), "r")
        # Parse deterministic observation timestamp from NetCDF metadata
        time_str = f.attrs.get("time_coverage_start")
        if isinstance(time_str, bytes):
            time_str = time_str.decode("utf-8")
        if time_str and isinstance(time_str, str):
            try:
                obs_time = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
            except Exception:
                obs_time = datetime(2026, 8, 29, 0, 0, 0, tzinfo=timezone.utc)
        else:
            obs_time = datetime(2026, 8, 29, 0, 0, 0, tzinfo=timezone.utc)
        if obs_time.tzinfo is None:
            obs_time = obs_time.replace(tzinfo=timezone.utc)

        if "flash_lat" in f and "flash_lon" in f:
            lats = f["flash_lat"][:]
            lons = f["flash_lon"][:]
            energies = f["flash_energy"][:] if "flash_energy" in f else np.zeros_like(lats)
            areas = f["flash_area"][:] if "flash_area" in f else np.zeros_like(lats)
            qf = f["flash_quality_flag"][:] if "flash_quality_flag" in f else np.zeros_like(lats)

            for lat, lon, energy, area, q in zip(lats, lons, energies, areas, qf):
                if np.isnan(lat) or np.isnan(lon) or lat < -90 or lat > 90 or lon < -180 or lon > 180:
                    continue
                env = ObservationEnvelope(
                    geometry={"type": "Point", "coordinates": [round(float(lon), 5), round(float(lat), 5)]},
                    observed_at=obs_time,
                    source=f"{satellite}-glm",
                    source_version="GLM-L2-LCFA-v2",
                    quality=1.0 if int(q) == 0 else 0.6,
                    payload_ref={
                        "energy_joules": round(float(energy), 2),
                        "area_sqkm": round(float(area), 2),
                        "quality_flag": int(q),
                        "satellite": satellite
                    }
                )
                envelopes.append(env)
    except (OSError, KeyError, ValueError, TypeError) as e:
        print(f"[GLM NetCDF Extract] Format warning: {e}")
    except Exception as e:
        print(f"[GLM NetCDF Extract] Unexpected parsing error: {type(e).__name__}: {e}")
    return envelopes


def extract_fdcf_from_netcdf_bytes(nc_bytes: bytes, satellite: str = "noaa-goes18") -> list[ObservationEnvelope]:
    """Extracts active fire hotspots from real ABI-L2-FDCF NetCDF payload using exact geostationary projection."""
    envelopes = []
    raw_sha256 = hashlib.sha256(nc_bytes).hexdigest()
    f = None
    try:
        f = h5py.File(io.BytesIO(nc_bytes), "r")
        time_str = f.attrs.get("time_coverage_start")
        if isinstance(time_str, bytes):
            time_str = time_str.decode("utf-8")
        if time_str and isinstance(time_str, str):
            try:
                obs_time = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
            except ValueError:
                raise ValueError('Invalid FDCF observation timestamp')
        else:
            raise ValueError('Missing FDCF observation timestamp')
        if obs_time.tzinfo is None:
            obs_time = obs_time.replace(tzinfo=timezone.utc)
        
        sub_lat = to_scalar_float(f.get("nominal_satellite_subpoint_lat"), 0.0)
        sub_lon = to_scalar_float(f.get("nominal_satellite_subpoint_lon"), -137.0)
        
        proj = f.get("goes_imager_projection")
        h_sat = to_scalar_float(proj.attrs.get("perspective_point_height") if proj else None, 35786023.0)
        req = to_scalar_float(proj.attrs.get("semi_major_axis") if proj else None, 6378137.0)
        rpol = to_scalar_float(proj.attrs.get("semi_minor_axis") if proj else None, 6356752.31414)
        origin_lon = to_scalar_float(proj.attrs.get("longitude_of_projection_origin") if proj else None, sub_lon)

        x_vals = f["x"][:] if "x" in f else None
        y_vals = f["y"][:] if "y" in f else None
        
        if x_vals is not None and "scale_factor" in f["x"].attrs:
            x_vals = x_vals * to_scalar_float(f["x"].attrs["scale_factor"]) + to_scalar_float(f["x"].attrs["add_offset"])
        if y_vals is not None and "scale_factor" in f["y"].attrs:
            y_vals = y_vals * to_scalar_float(f["y"].attrs["scale_factor"]) + to_scalar_float(f["y"].attrs["add_offset"])

        if "Power" in f:
            def decoded(name):
                dataset = f[name]
                raw = dataset[:]
                unsigned = dataset.attrs.get('_Unsigned', '')
                if unsigned in ('true', 'TRUE', b'true', b'TRUE') and raw.dtype.kind == 'i':
                    raw = raw.view(np.dtype(f'u{raw.dtype.itemsize}'))
                values = raw.astype(float)
                invalid = ~np.isfinite(values)
                for key in ('_FillValue', 'missing_value'):
                    if key in dataset.attrs:
                        invalid |= raw == to_scalar_float(dataset.attrs[key])
                if 'valid_range' in dataset.attrs:
                    low, high = np.asarray(dataset.attrs['valid_range']).reshape(-1)
                    invalid |= (values < low) | (values > high)
                values = values * to_scalar_float(dataset.attrs.get('scale_factor'), 1) + to_scalar_float(dataset.attrs.get('add_offset'), 0)
                values[invalid] = np.nan
                return values

            power = decoded('Power')
            temperature = decoded('Temp') if 'Temp' in f else None
            valid_y, valid_x = np.where(np.isfinite(power) & (power > 0))
            if len(valid_y) > 0:
                top_indices = np.argsort(power[valid_y, valid_x])[::-1]
                for idx in top_indices:
                    py, px = valid_y[idx], valid_x[idx]
                    frp_val = float(power[py, px])
                    
                    if x_vals is not None and y_vals is not None:
                        x_rad = float(x_vals[px])
                        y_rad = float(y_vals[py])
                    else:
                        x_rad = (px - 2711.5) * 56e-6
                        y_rad = (2711.5 - py) * 56e-6
                        
                    lat, lon = abi_scan_to_latlon(x_rad, y_rad, sub_lon_deg=origin_lon, h_sat=h_sat, req=req, rpol=rpol)
                    if lat is None or lon is None:
                        continue
                        
                    env = ObservationEnvelope(
                        geometry={"type": "Point", "coordinates": [lon, lat]},
                        observed_at=obs_time,
                        source=f"{satellite}-fdcf",
                        source_version="ABI-L2-FDCF-v3",
                        quality=1.0,
                        payload_ref={"frp": round(frp_val, 2), "temp": float(temperature[py, px]) if temperature is not None and np.isfinite(temperature[py, px]) else None, "satellite": satellite, "raw_sha256": raw_sha256}
                    )
                    envelopes.append(env)
    except (OSError, KeyError, ValueError, TypeError) as e:
        print(f"[FDCF NetCDF Extract] Format warning: {e}")
    except Exception as e:
        print(f"[FDCF NetCDF Extract] Unexpected parsing error: {type(e).__name__}: {e}")
    finally:
        if f is not None:
            f.close()
    return envelopes


def parse_fdcf_record(record: dict[str, Any], satellite: str = "noaa-goes18") -> ObservationEnvelope:
    obs_time = datetime.fromisoformat(record["observed_at"].replace("Z", "+00:00"))
    if obs_time.tzinfo is None:
        obs_time = obs_time.replace(tzinfo=timezone.utc)
    frp = float(record.get("frp", 0.0))
    temp = float(record.get("temp", 300.0))
    dqf = int(record.get("dqf", 0))
    quality = 1.0 if dqf == 0 else 0.5
    return ObservationEnvelope(
        geometry={"type": "Point", "coordinates": [float(record["lon"]), float(record["lat"])]},
        observed_at=obs_time,
        source=f"{satellite}-fdcf",
        source_version="ABI-L2-FDCF-v2",
        quality=quality,
        payload_ref={"frp": frp, "temp": temp, "area": record.get("area", 0.0), "dqf": dqf, "satellite": satellite}
    )


def parse_glm_record(record: dict[str, Any], satellite: str = "noaa-goes18") -> ObservationEnvelope:
    obs_time = datetime.fromisoformat(record["observed_at"].replace("Z", "+00:00"))
    if obs_time.tzinfo is None:
        obs_time = obs_time.replace(tzinfo=timezone.utc)
    energy = float(record.get("energy_joules", 0.0))
    area = float(record.get("area_sqkm", 0.0))
    quality_flag = int(record.get("quality_flag", 0))
    quality = 1.0 if quality_flag == 0 else 0.6
    return ObservationEnvelope(
        geometry={"type": "Point", "coordinates": [float(record["lon"]), float(record["lat"])]},
        observed_at=obs_time,
        source=f"{satellite}-glm",
        source_version="GLM-L2-LCFA-v2",
        quality=quality,
        payload_ref={"energy_joules": energy, "area_sqkm": area, "quality_flag": quality_flag, "satellite": satellite}
    )


def _read_fdcf_fixture(fixture_path: str | Path | None = None) -> list[ObservationEnvelope]:
    if fixture_path is None:
        fixture_path = Path("data/fixtures/goes_fdcf_sample.json")
    path = Path(fixture_path)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    sat = data.get("satellite", "noaa-goes18")
    return [parse_fdcf_record(r, sat) for r in data.get("fires", [])]


def _read_glm_fixture(fixture_path: str | Path | None = None) -> list[ObservationEnvelope]:
    if fixture_path is None:
        fixture_path = Path("data/fixtures/goes_glm_sample.json")
    path = Path(fixture_path)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    sat = data.get("satellite", "noaa-goes18")
    return [parse_glm_record(r, sat) for r in data.get("flashes", [])]


def ingest_fdcf_with_status(
    fixture_path: str | Path | None = None,
    live: bool = False,
    allow_fixtures: bool = False,
    prefix: str | None = None,
    satellite_bucket: str = GOES18_S3_BASE
) -> tuple[list[ObservationEnvelope], str]:
    """Ingests NOAA GOES ABI-L2-FDCF active fire hotspots with operational status reporting."""
    if live:
        key = None
        if prefix:
            keys = list_s3_anonymous_keys(satellite_bucket, prefix, max_keys=5)
            if keys:
                key = keys[-1]
        else:
            key = discover_latest_goes_s3_key("ABI-L2-FDCF", satellite_bucket)
            
        if not key:
            if allow_fixtures:
                return _read_fdcf_fixture(fixture_path), "fixture_fallback"
            return [], "unavailable_no_s3_key"
            
        nc_bytes = download_goes_netcdf(key, satellite_bucket, "lake/raw/noaa-goes18-fdcf")
        if not nc_bytes:
            if allow_fixtures:
                return _read_fdcf_fixture(fixture_path), "fixture_fallback"
            return [], "unavailable_download_failed"
            
        live_obs = extract_fdcf_from_netcdf_bytes(nc_bytes, "noaa-goes18")
        if live_obs:
            return live_obs, "ok"
        return [], "no_observations"

    return _read_fdcf_fixture(fixture_path), "fixture"


def ingest_fdcf(
    fixture_path: str | Path | None = None,
    live: bool = False,
    allow_fixtures: bool = False,
    prefix: str | None = None,
    satellite_bucket: str = GOES18_S3_BASE
) -> list[ObservationEnvelope]:
    """Ingests NOAA GOES ABI-L2-FDCF active fire hotspots."""
    obs, _ = ingest_fdcf_with_status(
        fixture_path=fixture_path,
        live=live,
        allow_fixtures=allow_fixtures,
        prefix=prefix,
        satellite_bucket=satellite_bucket
    )
    return obs


def ingest_glm_with_status(
    fixture_path: str | Path | None = None,
    live: bool = False,
    allow_fixtures: bool = False,
    prefix: str | None = None,
    satellite_bucket: str = GOES18_S3_BASE
) -> tuple[list[ObservationEnvelope], str]:
    """Ingests NOAA GOES GLM-L2-LCFA lightning flashes with operational status reporting."""
    if live:
        key = None
        if prefix:
            keys = list_s3_anonymous_keys(satellite_bucket, prefix, max_keys=5)
            if keys:
                key = keys[-1]
        else:
            key = discover_latest_goes_s3_key("GLM-L2-LCFA", satellite_bucket)
            
        if not key:
            if allow_fixtures:
                return _read_glm_fixture(fixture_path), "fixture_fallback"
            return [], "unavailable_no_s3_key"
            
        nc_bytes = download_goes_netcdf(key, satellite_bucket, "lake/raw/noaa-goes18-glm")
        if not nc_bytes:
            if allow_fixtures:
                return _read_glm_fixture(fixture_path), "fixture_fallback"
            return [], "unavailable_download_failed"
            
        live_obs = extract_glm_from_netcdf_bytes(nc_bytes, "noaa-goes18")
        if live_obs:
            return live_obs, "ok"
        return [], "no_observations"

    return _read_glm_fixture(fixture_path), "fixture"


def ingest_glm(
    fixture_path: str | Path | None = None,
    live: bool = False,
    allow_fixtures: bool = False,
    prefix: str | None = None,
    satellite_bucket: str = GOES18_S3_BASE
) -> list[ObservationEnvelope]:
    """Ingests NOAA GOES GLM-L2-LCFA lightning flashes."""
    obs, _ = ingest_glm_with_status(
        fixture_path=fixture_path,
        live=live,
        allow_fixtures=allow_fixtures,
        prefix=prefix,
        satellite_bucket=satellite_bucket
    )
    return obs
