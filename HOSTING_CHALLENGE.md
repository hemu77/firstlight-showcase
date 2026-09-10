# Making research predictions usable on free hosting

FirstLight's globe and its prediction pipeline have different lifecycles. The globe is served by Vercel. The Python prediction service runs in Docker on Render Free, which can sleep when idle and does not provide a durable local disk.

The initial deployment exposed a real integration gap: a working local model did not mean hosted predictions were available. Deploying the frontend alone did not deploy Python, its model artifacts, or its storage.

## The implemented approach

1. The prediction panel requests the last successful JSON snapshot through a Vercel server route. Its GitHub token is read-only and never sent to the browser.
2. Opening the panel also asks Render to refresh in the background. While the panel is visible, it checks stored results every 30 seconds and requests a refresh every five minutes.
3. Render allows one calculation at a time, throttles attempts, and skips calculation when the stored observation already covers the latest two-hour scan anchor.
4. A successful source-verified calculation writes a new snapshot to a private GitHub repository. GitHub's file SHA detects conflicting writes. Errors preserve the previous snapshot rather than inventing data or changing its original timestamp.
5. Separate repository-scoped credentials give Render write access and Vercel read-only access. Tokens expire on December 31, 2026 and must be rotated before then.

## What “live” means here

Hosted predictions refresh on demand while the prediction panel is in use—not continuously when nobody visits. A calculation already started may finish after a visitor leaves, or be interrupted by the free host. Cold starts and upstream outages can delay results. This is not a five-minute update guarantee.

Locally, the existing scheduler can run repeated ingestion and prediction checks while the local services are running with `FIRSTLIGHT_LIVE=1`. This is scheduled near-real-time processing, not instantaneous observations. NOAA source anchors used by this model are two hours apart, and weather features intentionally use a 24-hour lag to match training.

The map's observed event feeds and the fire model are separate. Global reported events do not imply global prediction coverage. Forecasts cover eligible western-US active-fire cells only. An empty eligible scan legitimately yields zero forecasts.

## Tradeoffs and boundaries

- GitHub holds only compact snapshots and private model artifacts—not the historical satellite lake. This is a low-traffic portfolio storage compromise, not a production database.
- Docker packages dependencies; it does not prevent sleep or create persistent storage. Satellite arrays are read in row chunks to reduce peak memory without changing the pixel-quality test.
- The public refresh endpoint takes no user-selected data URLs or model inputs. Its in-process throttle assumes one Render instance and one API worker. Scale-out would need a shared lease and distributed rate limiting.
- Snapshot age, source time, model hash, and research limitations remain visible. A successful deployment is not evidence of predictive accuracy.
- The model passed its August 2024 temporal/geographic ranking gates. Current-year transfer remains unvalidated; this is not an ignition, spread, earthquake, or emergency-warning system.

The public [backend directory](backend/) makes this hosting approach inspectable. Full UI source, credentials, training data, and model files are not published here.

## Deployment verification — September 10, 2026 (UTC)

- Render completed a source-processing run and wrote the private snapshot at **06:57:22 UTC**, using a NOAA observation at **06:00:21 UTC**. GitHub recorded the snapshot commit at **06:57:24 UTC**.
- The public Vercel prediction panel displayed that new calculation timestamp, confirming the Render → private GitHub → Vercel path, not merely a local run.
- This scan had **zero eligible forecast cells**. The application displayed zero instead of fabricated scores. This run verifies hosted processing/storage delivery, not nonempty current prediction accuracy.
- Ten focused snapshot/model tests passed; TypeScript and the Vercel build passed. This is bounded verification, not a claim of zero bugs or a full security audit.
- A deployment issue exposed a stale credential remaining in an asynchronously loaded masked secret field. A successful save was insufficient evidence; verifying the persisted value privately and then an authenticated runtime operation resolved it. No token values are published.

[Open FirstLight](https://firstlight-sai-hemanth-kilaru-geo-vercel.vercel.app/) · [Prediction service health](https://firstlight-predictions.onrender.com/healthz)
