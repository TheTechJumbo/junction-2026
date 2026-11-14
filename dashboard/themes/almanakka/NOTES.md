# Almanakka

- **Idea:** a page of the family almanac. The hero is an ink drawing of Anna's kitchen corner (window, wall almanac, kettle, weekly pill box, front door) that draws itself in on load.
- **Moves with:** the sun crosses the window and the pane follows `data-sky`. Red is the almanac's red letter and appears only in `notice`.
- **Type:** Schibsted Grotesk with IBM Plex Mono for times and labels. Palette tokens at the top of `theme.css`.
- `source/gen.py` draws the SVGs with a seeded wobble; every path has `pathLength="1"` for the draw-in.
