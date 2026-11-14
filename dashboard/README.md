# Daylight family dashboard

Buildless HTML/CSS/ES modules; no dependency installation or external assets required.
Use Node 18+ for `npm --prefix dashboard run check`. Foundation gates remain `make setup`
and `make check` from the repository root.

## Serve with the API

Serve this directory at `/dashboard/` on the **same origin** as the API. Mount `/timeline`,
`/summary`, and local evidence paths at the origin root. Open `/dashboard/`, click Connect,
and enter the configured API bearer token. It exists only in JavaScript memory for this
tab and is cleared on reload/disconnect. Never embed credentials in HTML, source, URLs,
logs, or browser storage. All authenticated requests use the single `ApiClient`.
A server/reverse proxy should disable access logging of Authorization headers and serve
these files with no-store caching for private household sessions.

For UI-only preview: `python3 -m http.server 8087 --bind 127.0.0.1 --directory dashboard`.
Open http://127.0.0.1:8087 and explicitly choose **Explore synthetic demo**. Fixture mode
is visibly marked and never used as an API error fallback. It has a fixed example day;
leave demo to select a live day. This static server does not implement the API.

Timeline and summary load together on date selection/Refresh. Europe/Helsinki is fixed
for day and clock display. The dashboard displays backend alerts, question counts and
sensor statuses without implementing their rules. Missing observations do not imply
medication ingestion, locking, safety, or normality. Image evidence is fetched with
Authorization only from the same origin; redirects, query parameters, URL credentials,
unsupported media, and external URLs are rejected. Browser blob URLs are revoked when
the evidence dialog closes, the day changes or the session ends.

## Browser checks

Check wide and 390px layouts, keyboard focus, native date picker, Connect dialog submit /
Escape, explicit demo label, refresh, empty API arrays, API/network errors, offline banner,
unknown/offline sensors, and evidence success/404/unsupported URL. Inspect Network for
Bearer headers and absence of token URLs. No automatic live polling is implemented.
Actual backend integration must be verified after route wiring by the integrator.
