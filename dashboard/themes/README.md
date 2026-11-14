# Themes

A theme (`themes/<id>/`) restyles the same page (`index.html`, `app.js`): colour, type, layout,
motion and imagery. It is listed in `themes.json` and picked with **Look** or `?theme=<id>`.
The reader is Sara checking on her mother Anna in a spare minute: calm and warm, never clinical,
alarming or surveillance-like.

```
theme.css          the whole stylesheet (base.css only resets and hides things)
assets/mark.svg    logo mark, square, inserted twice, so no ids
assets/scene.svg   hero illustration, inlined so theme.css can colour and animate it
assets/icons.svg   <symbol id="i-NAME" viewBox="0 0 24 24"> drawn with currentColor
fonts/             at most four Latin woff2 files (OFL, from @fontsource) + LICENSE.txt notices
NOTES.md           a few lines: the idea, what moves, the type
source/            optional generator for the SVGs
```

Draw the SVGs first and take the palette tokens in `theme.css` from them. The OFL text all the
fonts share is `OFL.txt`.

## Rules (the page's CSP enforces most)

- SVGs have no `<style>`, `style=""`, `<script>`, `<foreignObject>` or external links (they are
  stripped when inlined). Ids in `scene.svg` and non-symbol ids in `icons.svg` start with `<id>-`.
- Icons: `pill door eye question alert sensor calm sun moon theme info refresh install
  chevron-left chevron-right`.
- No external URLs in CSS. Set `--theme-color` on `:root`. Don't define custom properties named
  like the hooks below.
- Phone first (390 px, no sideways scroll, 16 px gutters), a real wide layout at 1440 px, body
  text 15 px or more, WCAG AA contrast, `tabular-nums` on times and counts, safe-area insets.
- Motion follows the real sky and the mood, stays gentle, and nothing needs it to be read.
  The entrance reveal is under 600 ms and never leaves content hidden.

## Hooks

On `<html>`:

| Hook | Values |
| --- | --- |
| `data-mood` | `unknown` `quiet` `calm` `notice` (backend raised alerts) |
| `data-mode` | `disconnected` `demo` `live` |
| `data-sky` | `day` `low-sun` `twilight` `dark` (real Helsinki sky; dark is normal in November) |
| `data-daypart` | `night` `morning` `afternoon` `evening` |
| `data-day-state` | `today` `past` `future` |
| `data-loading` | present while loading |
| `--now`, `--sunrise`, `--sunset` | 0 to 1, fraction of the day |
| `--sun-progress` | 0 at sunrise to 1 at sunset, clamped |
| `--sun-altitude` | 1 at noon, 0 at the horizon, below 0 at night |

On elements:

- `.rhythm-marks > .mark`: `--at`, `data-kind` (`pills door vision question other`),
  `data-flag="alert"`, an icon and `.mark-label`. `.rhythm-now` (show only when `today`),
  `.rhythm-daylight`, `.rhythm-parts > span[data-part]`, `.rhythm-hours > span`.
- `.events > li.group[data-part]` and `li.event` (`data-kind`, `data-source`, `data-flag`) with
  `time`, `.event-dot`, `.event-icon`, `.event-body`.
- `.alerts > .alert[data-type]`, `.question` (`--count`, `.pips > .pip`), `.sensor[data-status]`,
  `.empty` in any empty list.
- `.hero-actions` shows only when disconnected, and then `#content` is hidden: hero, sun card and
  day bar must stand alone.

Style every element in `index.html`, including the dialogs and `:focus-visible`.
