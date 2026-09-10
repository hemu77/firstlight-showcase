# FirstLight backend API reference

By SAI HEMANTH KILARU

This is a documentation-only reference to the Python backend. The public showcase does not contain a runnable server, model artifacts, or training data. The Vercel globe is deployed separately; its hosted prediction backend is not yet provisioned. These paths are not a claim that a public API is currently available.

## Implemented Python routes

| Method and path | Purpose |
| --- | --- |
| `GET /` | Basic service response. |
| `GET /v1/health/data` | Source freshness, observation counts, ingestion status, model checksum and snapshot status. |
| `GET /v1/predictions` | Most recent validated fire-intensity inference snapshot. |
| `GET /v1/events` | Folded event records; accepts `status`, `min_frp`, and `since`. |
| `GET /v1/events/{event_id}` | Event details by event identifier or H3 resolution-5 cell; absent records return 404. |
| `GET /v1/coverage/grid` | Cached observation-demand grid. |
| `GET /v1/coverage/score` | Geometric constellation coverage analysis using satellite count, plane count, inclination and altitude. Not detection probability. |
| `GET /v1/ledger` | Legacy prediction-ledger interface; not the model-inference endpoint used by the globe. |
| `GET /v1/fires` | Raw fire observations from the local Parquet lake. |

## Prediction response fields

- `status`: `live`, `stale`, or `unavailable`.
- `generated_at`: inference computation timestamp.
- `observed_at`: satellite observation timestamp.
- `model_sha256`: model artifact checksum.
- `withheld_cells`: candidates withheld for missing inputs.
- `predictions`: rows containing `h3`, `probability`, `target_at`, `weather_valid_at`, and numerical `features`.

Some metadata can be absent when unavailable. A successful HTTP response is not proof of fresh data: clients must examine status and timestamps. Missing observations must not be replaced with invented values.

The research target is at least one ≥100 MW fire pixel in an eligible western-US cell at the next-day scan. It is not wildfire ignition, spread, earthquake forecasting, or emergency guidance. Model scope and validation limitations are described in the main README.

## Hosting connection

Run the Python service on a provisioned host with the approved model and persistent storage. Configure Vercel's server-side `FIRSTLIGHT_API_ORIGIN` with that host's HTTPS origin and rebuild. Never configure deployed Vercel rewrites to use localhost.

The globe checks predictions every five minutes; provider observations and inference can have a different cadence. Uploading this reference to GitHub does not execute inference or provide an always-running API. See [deployment requirements](DEPLOYMENT.md).
