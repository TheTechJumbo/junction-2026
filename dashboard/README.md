# Tuttu family dashboard

Buildless HTML, CSS and ES modules with no dependencies. `npm --prefix dashboard run check` runs
the syntax checks and Node tests (Node 18+).

- **Serve** at `/dashboard/` on the same origin as the API (`backend/run.py` does). Connect asks
  for the API bearer token, which lives only in memory for that tab; never put it in HTML, URLs,
  logs or storage. All requests go through `ApiClient` in `client.js`. Evidence images are fetched
  same-origin only; redirects, query strings and external URLs are refused.
- **Preview** without the API: `python3 -m http.server 8087 --directory dashboard`, then choose
  **Explore synthetic demo**. URL options: `?demo` (example day), `?demo=calm`, `?theme=<id>`,
  `?at=HH:MM` (scene at another time), `?nosw` (skip the offline cache).
- **Day and looks:** times are Europe/Helsinki; `day.js` works out sunrise and sunset locally. The
  mood comes only from backend alerts; the page applies no care rules and a missing observation
  means nothing was recorded, not that nothing happened. Looks live in `themes/` (see its README).
- **Install:** `manifest.json` and `icons/` make it installable over HTTPS or on localhost.
  `sw.js` caches only the app's own files, never API responses, evidence or anything with the token.
  The Android app in `android/family` packages this folder.

## Data provenance
Live mode requests `/timeline` and `/summary`; it never falls back to examples on failure.
Example day explicitly loads synthetic JSON from `fixtures/`, with a fictional home name.
Live API records may include test events previously ingested into that backend.
The scene is decorative, not a live view of the home. Helsinki time and sunrise coordinates
are fixed defaults, not a detected wearer location. No alerts is not an assessment of wellbeing.
