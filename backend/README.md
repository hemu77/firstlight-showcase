# FirstLight visitor-triggered prediction API

This directory contains the deployed prediction-serving code, including its source-processing dependencies. Training is not run by the service. Model artifacts and snapshots stay in the private `firstlight-predictions` repository; no credentials are included here.

Render Free, Docker runtime, one worker. Set `FIRSTLIGHT_GITHUB_TOKEN` through the FirstLight-only environment group. `GET /healthz` reports calculation state. `POST /v1/predictions/refresh` starts at most one calculation per five minutes per process; an unchanged two-hour scan anchor skips calculation. `GET /v1/predictions` returns the last stored snapshot.

The public refresh endpoint accepts no user parameters and cannot choose model files, URLs, or repository paths. It is globally throttled but is not an authenticated private API. A paid multi-instance deployment would require a shared lock and rate limiter. A restart clears the in-memory cooldown; GitHub's content SHA prevents silently overwriting a competing write.

Missing inputs fail closed; old snapshots remain visibly stale. Free hosting can interrupt a calculation. There is no five-minute freshness SLA. Predictions are western-US fire-intensity research estimates, not emergency guidance or earthquake forecasts.
