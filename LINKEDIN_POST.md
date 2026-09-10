# LinkedIn launch post

Open Earth data. A $0-tier infrastructure budget. One connected geospatial workspace.

I built FirstLight — Earth Under Observation to bring event reports, fire-weather outlooks, satellite geometry, and street-level 3D into the same investigation flow.

Start in orbit. Find a reported event. Check its source and timestamp. Explore the surrounding city in 3D—without switching applications.

What it brings together:

• Wildfire reports, earthquakes, US weather alerts, and cyclone reports from NASA EONET, USGS, NOAA/NWS, and GDACS.
• Date filters and replay for available event history.
• Seven-day ECMWF-based fire-weather danger maps through Copernicus GWIS.
• Satellite orbit simulation and geometric access calculations using SGP4.
• Real OpenStreetMap buildings, streamed on demand through Cesium.
• A separate experimental fire-ranking model using NOAA GOES-18 and Open-Meteo, limited to eligible western-US cells.

The hard part was not adding more layers. It was keeping their meaning intact.

Providers publish on different schedules. Free backends sleep. A successful HTTP response can still contain an unusable map. And a working local model does not automatically become a working deployed service.

FirstLight validates provider imagery, preserves the last successful prediction snapshot during refresh, exposes freshness and coverage limits, and streams 3D only when requested. Hosted model refreshes are visitor-triggered—not disguised as continuous computation.

Stack: Next.js, React, TypeScript, CesiumJS, satellite.js/SGP4, H3, Python/FastAPI, and Docker.

Deployment: Vercel Hobby + Render Free, with compact prediction snapshots stored privately on GitHub and read through a server route.

No paid data subscriptions. Free-access APIs and free infrastructure tiers—with quotas treated as design constraints, not ignored until deployment.

I also kept one distinction explicit: fire-weather danger is not ignition probability; satellite access is not guaranteed detection; experimental ML is not emergency guidance.

Try it: https://firstlight-sai-hemanth-kilaru-geo-vercel.vercel.app/

Architecture, fresh recordings, and selected backend code: https://github.com/hemu77/firstlight-showcase

I’m SAI HEMANTH KILARU, open to software engineering and geospatial/data opportunities. If your team works on spatial systems or reliable data products, I’d welcome a conversation.

skilaru@arizona.edu

#Geospatial #SoftwareEngineering #DataEngineering #OpenToWork

---

Claim notes: "$0-tier" describes the selected plans, not audited lifetime spending. Provider and hosting sources are linked in README. No reach, complete-coverage, forecast-accuracy, or 60-FPS guarantee is implied.
