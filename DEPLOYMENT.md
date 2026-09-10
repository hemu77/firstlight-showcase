# Deployment requirements

GitHub stores the source; it does not run an always-on Python server. This public repository is a showcase and intentionally cannot build the full private application.

## Private backend deployment

The private project includes `infra/Dockerfile.api` and `infra/compose.production.yml`. The Dockerfile installs Python dependencies and runs:

```sh
python -m uvicorn services.api.app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

Deploy that image on a container host with HTTPS and persistent storage mounted at `/app/lake`. Provision the validated model directory and required model metadata privately; do not upload model weights, raw datasets, or credentials to this repository. Confirm the host has enough memory for the satellite-processing pipeline.

Enable `FIRSTLIGHT_LIVE=1` only after model provisioning and a successful one-shot `python -m pipelines.live_predictions`. Set `CORS_ALLOWED_ORIGINS` to the actual frontend origin. Keep a single scheduler instance to avoid duplicate ingestion jobs.

Set Vercel's `FIRSTLIGHT_API_ORIGIN` to the hosted HTTPS API origin and rebuild the frontend. Never use localhost for the deployed API origin.

## Acceptance checks

- `/v1/health/data` reports the source/model status.
- `/v1/predictions` returns schema-valid, source-timestamped inference.
- A restart retains the model and last successful snapshot.
- A provider outage retains previous results with a stale label.
- Selecting a forecast on the deployed globe opens its real cell and details.
- No credentials or private artifacts appear in browser bundles or public Git history.

This requires a separately provisioned host. GitHub Actions could run finite publishing jobs in a different architecture, but is not an always-on API server or a guarantee of five-minute delivery. No such architecture is configured by this showcase.
