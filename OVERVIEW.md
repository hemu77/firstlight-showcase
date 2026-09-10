# FirstLight overview

By SAI HEMANTH KILARU

FirstLight combines reported natural events, geographic exploration, simulated satellite access, and experimental next-day fire-intensity rankings in one globe.

## What visitors can explore

The deployed interface now also provides seven-day ECMWF/Copernicus GWIS fire-weather outlooks and on-demand Cesium OSM Buildings. The provider outlook is separate from FirstLight ML; it is Fire Weather Index, not a probability of ignition. The 3D toggle streams nearby reference buildings only when requested below 15 km. It pauses at orbital altitude and in hidden tabs, retains a base-map fallback, and respects the Community streaming allowance. A saved NYC example is shown only near New York.

- Navigate from orbit to cities and reported locations.
- Understand marker colors using the map key.
- Filter supported event history and replay observations.
- Inspect provider timestamps, attribution, and stale-data notices.
- Explore hypothetical satellite geometry, clearly separated from actual observations.
- Inspect research forecasts when the prediction backend is available.

## Engineering approach

Next.js and Cesium render the interface. Public-provider adapters normalize observations. A separate Python pipeline produces validated forecast snapshots; the browser does not receive model weights or training datasets. A single animation-frame loop smooths camera input, and motion respects reduced-motion preferences.

## Boundaries

Global coverage is incomplete and provider-dependent. Replay is not a reconstruction of unreported events. Satellite access is geometric, not a promise of detection. The fire model does not predict ignition or spread and is not emergency guidance. Hosted predictions require a separately configured backend.

The public sample demonstrates bounded camera interpolation and idle-rotation gating. Main application code, model artifacts, credentials, and raw datasets are intentionally excluded. Browser-delivered assets and returned observations are necessarily visible to visitors.
