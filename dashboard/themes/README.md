# Daylight themes

Every theme is a folder `themes/<id>/` that changes the whole look of the same page (`index.html`,
`app.js`). The page, data and behaviour are shared. A theme owns colour, type, layout, motion and
imagery. Themes are listed in `themes.json` and picked with the **Look** button, or `?theme=<id>`.

## Who it's for

Sara checks on her mother Anna, who has dementia and lives alone in Helsinki. Anna wears memory
glasses; a pill-box sensor and a front-door sensor are in her flat. Sara opens Daylight on her
phone, often in a spare minute, sometimes worried. The page's one job: tell her at a glance
whether today looks calm or needs a look, then let her see what happened and when. The tone is
calm and warm, never clinical or alarming, and never surveillance-like.

## Files

```
themes/<id>/
  theme.css            the whole stylesheet for this look (base.css only resets and hides things)
  assets/mark.svg      the logo mark (square viewBox). Shown in the header and footer.
  assets/scene.svg     the hero illustration. It is inlined, so theme.css can colour and animate it.
  assets/icons.svg     an icon sprite of <symbol>s (list below)
  fonts/*.woff2        self-hosted fonts, plus fonts/LICENSE.txt (SIL OFL only)
  NOTES.md             five to ten lines: the idea, the palette and where it came from, the type
  source/              optional: the script that draws the SVGs, if they were generated
```

## Work from the assets

Draw the SVGs first, then take the palette from them. The colour tokens at the top of
`theme.css` must be the same colours the assets use, named for where they came from in the
drawings (for example `--lamp: #f2b04a; /* window lamp in scene.svg */`). The type and the shapes
(corners, line weights, rhythm) should also follow the drawings. The page should look like it was
designed around its illustration, not like a template with a picture dropped in.

## Hard rules (the page's Content-Security-Policy enforces most of them)

- SVGs: no `<style>` elements, no `style=""` attributes, no `<script>`, no `<foreignObject>`, no
  links to other files. Use presentation attributes (`fill`, `stroke`, ...) and `class` names that
  theme.css styles. They are removed when the SVG is inlined, so they would simply be lost.
- Any `id` inside `scene.svg` (gradients, clip paths, masks) starts with `<id>-`; inside
  `icons.svg` the symbols keep their required `i-NAME` ids and anything else starts with `<id>-`.
  `mark.svg` is inserted twice, so it must contain no `id` at all.
- Don't name your own custom properties after the page's hooks below (`--now`, `--at`, `--count`,
  `--sun-*`, `--sunrise`, `--sunset`): the script sets them on `<html>` and would overwrite yours.
- `icons.svg` contains exactly these symbols, each `<symbol id="i-NAME" viewBox="0 0 24 24">`,
  drawn with `currentColor` so CSS can colour them: `pill`, `door`, `eye` (seen by the glasses),
  `question`, `alert`, `sensor`, `calm` (nothing to worry about), `sun`, `moon`, `theme`,
  `refresh`, `install`, `chevron-left`, `chevron-right`.
- No external URLs in CSS. Fonts are self-hosted woff2 in `fonts/`. Fetch them from the
  @fontsource packages on jsDelivr, for example
  `https://cdn.jsdelivr.net/npm/@fontsource/<family>@5/files/<family>-latin-<weight>-normal.woff2`
  (check the exact file name in the package listing at
  `https://cdn.jsdelivr.net/npm/@fontsource/<family>@5/files/`). Latin subset, at most four files,
  at most 250 KB in total, OFL only, and keep a real fallback stack.
- `theme.css` sets `--theme-color` on `:root` (the phone's browser-bar colour).
- Edit only files inside your own `themes/<id>/`. If the shared markup or script stops you doing
  something, say what you need in your report instead of changing it.

## Hooks the page gives you

On `<html>`, kept up to date by the script:

| Hook | Values | Meaning |
| --- | --- | --- |
| `data-mood` | `unknown` `quiet` `calm` `notice` | No data yet / nothing recorded / observations, no alerts / the backend raised alerts |
| `data-mode` | `disconnected` `demo` `live` | Not signed in / example day / real data |
| `data-sky` | `day` `low-sun` `twilight` `dark` | The real sky over Helsinki right now |
| `data-daypart` | `night` `morning` `afternoon` `evening` | The current Helsinki time |
| `data-day-state` | `today` `past` `future` | The day being viewed, compared with today |
| `data-loading` | present while loading | |
| `--now` | 0 to 1 | Current time as a fraction of the day |
| `--sun-progress` | 0 to 1 | Sun's path from sunrise (0) to sunset (1), clamped |
| `--sun-altitude` | -1 to 1 | 1 at noon, 0 at the horizon, below 0 at night |
| `--sunrise`, `--sunset` | 0 to 1 | Today's (or the viewed day's) sunrise and sunset as fractions of the day |

Helsinki in November has about 7.5 hours of daylight (08:15 to 15:55), so for most of the day
the real sky is dark. Design for that: dark is the normal state, not an edge case.

On elements (set by the script):

- `.rhythm-marks > .mark` has `--at` (0 to 1, time of day), `data-kind` (`pills` `door` `vision`
  `question` `other`) and `data-flag="alert"` when the backend linked an alert to it. Each mark
  contains an icon and a `.mark-label` with a `<time>`. Lay the marks out with `--at`, for example
  `left: calc(var(--at) * 100%)`. A radial layout works too: `rotate(calc(var(--at) * 1turn))`.
- `.rhythm-now` is the current time line; place it with `--now`, and hide it unless
  `data-day-state="today"`. `.rhythm-daylight` is the daylight band; use `--sunrise`/`--sunset`.
  `.rhythm-parts > span[data-part]` and `.rhythm-hours > span` are the labels.
- `.events > li.group[data-part]` are daypart headings. `.events > li.event` has `data-kind`,
  `data-source` (`sensor` `vision` `voice`) and `data-flag="alert"`. It contains `<time>`,
  `.event-dot`, `.event-icon`, and `.event-body` (`strong`, `.event-where`, `.event-meta` with
  `.chip` and optional `.link-button`).
- `.alerts > .alert[data-type]` with `.alert-icon` and `.alert-body`; or `.empty` with an icon.
- `.question` has `--count`, `.question-words`, `.pips > .pip` (one per time asked, up to 12),
  `.question-count`.
- `.sensor[data-status]` (`online` `offline` `unknown`) with `.sensor-words` and `.state`.
- `.empty` (icon + text) appears in any list with nothing in it.
- `.hero-actions` is shown only when disconnected. When disconnected, `#content` is hidden, so the
  hero, the sun card and the day bar must make a complete, good-looking page on their own.

Every element in `index.html` needs styling, including the three dialogs (connect, evidence,
look picker with `.theme-option`, `.swatches > .swatch`, `.theme-words`), `.status` and
`.status.error`, the buttons (`.primary`, `.ghost`, `.icon-button`, `.link-button`), the day bar,
`.mode-label`, the footer, the skip link, and visible `:focus-visible` states.

## What "dynamic" means here

- The scene follows the real time of day. Move the sun or moon, light windows, and change the sky
  using `--sun-progress`, `--sun-altitude`, `data-sky` and `data-daypart`. Use CSS `calc()` with
  the variables, `transition`s for smooth changes, and a few gentle keyframe loops (snow, a flicker,
  breathing light). Nothing may be needed to read the page; it must be complete with motion off.
- The page responds to `data-mood`: a calm day and a day with something to notice should feel
  different at a glance (not only a different number).
- Entrance: a short, orchestrated reveal when the content renders is welcome. Keep it under
  about 600 ms in total, and never leave content at `opacity: 0` waiting for a trigger.

## Quality bar

- Phone first: 390 px wide with no sideways scrolling, 16 px side gutters at least, and the hero
  readable in the first screen. Then a considered wide layout at 1440 px, not a stretched phone.
- Running text about 65 characters wide at most. `font-variant-numeric: tabular-nums` on times
  and counts. Body text at least 15 px; contrast at least WCAG AA.
- Safe areas when installed: `env(safe-area-inset-*)` on the header and footer.
- Avoid the looks that read as machine-made: warm cream with a serif display and terracotta;
  near-black with one acid-green accent; purple-blue gradients; emoji; everything centred; every
  block a rounded card with a shadow; coloured rail on rounded cards; Inter or Space Grotesk.

## Checking your work

The API server is already running on port 8000; don't start or stop it. Use `shot.mjs`: it sets a
true viewport width (headless Chrome windows can't go below 500 px, so the older `shot.sh` fakes
phone widths), prints console errors, and reports sideways overflow. Pass `full` as the fifth
argument for a full-page capture. `?demo=calm` loads an example day with no alerts.

```sh
SHOT=C:/Users/Edwar/AppData/Local/Temp/claude/8e0ba1155c689eb4/f7f12f2b-3c0a-4061-b3d0-6af5eac7d696/scratchpad/shot.mjs
node $SHOT "http://127.0.0.1:8000/dashboard/?theme=<id>&demo&nosw&at=09:30" out.png 1440 1000 full
node $SHOT "http://127.0.0.1:8000/dashboard/?theme=<id>&demo=calm&nosw&at=21:00" out.png 390 844 full
```

The older script, for reference:

```sh
SHOT=C:/Users/Edwar/AppData/Local/Temp/claude/8e0ba1155c689eb4/f7f12f2b-3c0a-4061-b3d0-6af5eac7d696/scratchpad/shot.sh
$SHOT "http://127.0.0.1:8000/dashboard/?theme=<id>&demo&nosw&at=09:30" out.png 1440 2400   # example day, morning
$SHOT "http://127.0.0.1:8000/dashboard/?theme=<id>&demo&nosw&at=21:00" out.png 390 3200    # phone, evening
$SHOT "http://127.0.0.1:8000/dashboard/?theme=<id>&nosw&at=13:00" out.png 390 1400         # not connected
```

`?demo` loads the example day, `?at=HH:MM` previews the scene at that time, and `?nosw` skips
the offline cache. Look at the screenshots and fix what you see. Check at least: desktop and phone,
day and night, demo and not connected.
