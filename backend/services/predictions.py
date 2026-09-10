"""Visitor-triggered prediction service; one bounded calculation at a time."""
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from fastapi import FastAPI
from pipelines.snapshot_store import read_remote, read_file, should_refresh, write_remote

app = FastAPI(title='FirstLight prediction service', version='1.1.0')
lock = threading.Lock()
state = {'refresh':'idle', 'last_attempt':0.0, 'last_success':None}


def calculate():
    try:
        try:
            previous = read_remote()
        except Exception:
            previous = None
        if previous is not None and previous.get('publisher') == os.getenv('RENDER_SERVICE_ID','local') and not should_refresh(previous):
            state['refresh'] = 'unchanged'
            return
        directory = Path(os.getenv('FIRSTLIGHT_MODEL_DIR','lake/models/frp-history-2024'))
        directory.mkdir(parents=True, exist_ok=True)
        for name in ('model.txt','evaluation.json'):
            (directory/name).write_bytes(read_file('model/'+name))
        # Keep the HTTP process responsive; bound execution and suppress credential-bearing errors.
        result = subprocess.run([sys.executable,'-m','pipelines.live_predictions'],
            timeout=720, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode:
            raise RuntimeError('Calculation failed')
        import json
        data = json.loads(Path(os.getenv('FIRSTLIGHT_PREDICTIONS_PATH','lake/predictions/latest.json')).read_text())
        data['publisher'] = os.getenv('RENDER_SERVICE_ID','local')
        write_remote(data)
        state.update(refresh='complete', last_success=data['generated_at'])
    except Exception as exc:
        print('Prediction refresh failed:', type(exc).__name__, getattr(getattr(exc,'response',None),'status_code',None), flush=True)
        state['refresh'] = 'failed'
    finally:
        lock.release()


@app.get('/healthz')
def health():
    return {'service':'FirstLight predictions', **state, 'mode':'visitor-triggered'}


@app.post('/v1/predictions/refresh', status_code=202)
def refresh():
    # ponytail: single Render Free instance/worker; use a durable lease before scaling.
    if lock.acquire(blocking=False):
        if time.monotonic()-state['last_attempt'] < 300:
            lock.release()
        else:
            state.update(refresh='running', last_attempt=time.monotonic())
            threading.Thread(target=calculate, daemon=True).start()
    return {'refresh':state['refresh'], 'last_success':state['last_success']}


@app.get('/v1/predictions')
def predictions():
    try:
        return read_remote()
    except Exception as exc:
        print('Snapshot read failed:', type(exc).__name__, getattr(getattr(exc,'response',None),'status_code',None), flush=True)
        return {'status':'unavailable','predictions':[]}
