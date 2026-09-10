"""Acquire the predeclared fresh September holdout without replacing the August study."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pandas as pd
import httpx
import xml.etree.ElementTree as ET
from pipelines.rebuild_training import source_url


def download(anchor, directory=Path('lake/raw/holdout-september')):
    directory.mkdir(parents=True, exist_ok=True)
    prefix = f'ABI-L2-FDCF/{anchor:%Y}/{anchor:%j}/{anchor:%H}/'
    with httpx.Client(timeout=30) as client:
        response = client.get('https://noaa-goes18.s3.amazonaws.com/', params={'prefix':prefix, 'max-keys':1})
        response.raise_for_status()
        keys = ET.fromstring(response.content).findall('.//{*}Key')
        if not keys:
            raise ValueError(f'Missing source scan: {prefix}')
        name = keys[0].text.rsplit('/', 1)[1]
        path = directory / name
        if not path.exists():
            response = client.get(source_url(name))
            response.raise_for_status()
            temporary = path.with_suffix('.tmp')
            temporary.write_bytes(response.content)
            temporary.replace(path)
    return name


if __name__ == '__main__':
    anchors = pd.date_range('2026-08-31', '2026-09-09', freq='2h', inclusive='left', tz='UTC')
    with ThreadPoolExecutor(max_workers=4) as pool:
        for count, _ in enumerate(pool.map(download, anchors), 1):
            if count % 12 == 0:
                print(f'Downloaded/cached {count}/{len(anchors)} fresh scans', flush=True)
