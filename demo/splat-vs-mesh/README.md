# Splat vs mesh

Three views side by side, in the family app's four looks (Kaamos by default; the Look picker, or `?theme=`):
**1 real life → 2 Gaussian splat → 3 the block mesh our code makes from that splat**, with last night's walk
replayed over the mesh (trail in the air, neon orb, time controls) under all three. Home (or `h`) goes back
to the start view; Quality switches the mesh between high, medium and low.

| Scene | 1 | 2 | 3 |
|---|---|---|---|
| **Example home** (default, `?scene=home`) | the living room as built: 6.8k triangles, laptops, plant pots, books... | `home/home.splat`, 236k Gaussians sampled off it | `home/blocks_*.ply`, 724 / 460 / 216 triangles |
| **Real room** (`?scene=room`) | 12 of the public Mip-NeRF 360 "room" photos | the 1.59 M-Gaussian splat trained on them | `room/room_blocks_*.ply` |

On the example home, **Whole house** draws the rest of the house see-through around the living room (bathroom,
utility, hall, study, spare bedroom, garden; `home/house_*.ply`, room names in `home/house.json`): only the
living room was scanned, so the splat panel shows it alone.

```sh
python home.py                                   # the example home: actual.ply, home.splat, blocks_*.ply, house_*
python room_fetch.py                             # the real room: room.splat + 12 photos (public, never committed)
python room_convert.py room/room.splat room && python night_demo.py room
python -m http.server 8093                       # from the repo root, then open /demo/splat-vs-mesh/
```

### Assets for the video

| What | Where |
|---|---|
| Real photos | `room/photos/*.jpg` (+ `photos.json`, with the credit line: Mip-NeRF 360, Barron et al. 2022) |
| Gaussian splats | `room/room.splat`, `home/home.splat` (32-byte `.splat` records: any splat viewer opens them) |
| Meshes | `room/room_blocks_fine.ply`, `home/actual.ply`, `home/blocks_high.ply`, `home/house_*.ply` (binary PLY, vertex colours, z up) |
| Camera starts | `home/camera.json`, `room/camera.json` |
| The night walk | `home/night.json`, `room/night.json` |
| Page bits | `house.js` (see-through house + labels), `night.js` (the replay), `../../dashboard/space.js` (the app's viewer) |

`window.demo.space` / `.actual` / `.nightView` are the page's viewers, for scripting a recording: e.g.
`demo.space.turn(0.3, 0)`, `demo.nightView.play(1)`.

## The venue splat (`?scene=venue`, off the scene switch)

Seb's Gaussian splat of the Junction hacking hall (made from our Ray-Ban Meta glasses, people
anonymised) next to the block mesh our code makes from it, in the family app's own viewer, with the
"Last night" overlay (trail in the air, neon orb, time controls) playing over it.

```sh
# 1. the splat (33 MB, Git LFS on Seb's all-in-one branch; anonymised and approved for publishing)
git fetch origin all-in-one
git lfs fetch origin all-in-one --include=ml/recon_change/venue/venue-kitchen-glasses-anon.splat
git show origin/all-in-one:ml/recon_change/venue/venue-kitchen-glasses-anon.splat | git lfs smudge > demo/splat-vs-mesh/venue/venue-kitchen-glasses-anon.splat
# 2. serve the repo root and open http://127.0.0.1:8092/demo/splat-vs-mesh/
python -m http.server 8092
```

The venue's block meshes, example night and camera are committed (`venue/`). To rebuild them:
`python venue.py venue/venue-kitchen-glasses-anon.splat venue` then `python night_demo.py venue`.

- `venue.py` levels the splat (its flattest direction, checked against the camera's "down"), sets
  the scale from the glasses' height (the first camera is about 1.6 m above the floor; the floor is
  the lowest band, not the desk tops) and writes the block meshes at high and medium quality.
- `night_demo.py` makes up a night walk on the mesh's open floor (a grid path round the blocks) and
  runs `night.py` on it. It is an example, not a recording.
- `?scene=room` shows the public Mip-NeRF 360 "room" instead: run `python room_fetch.py` (or put `room.splat`
  (huggingface.co/cakewalk/splat-data) in `room/`, run `python room_convert.py room/room.splat room`
  and `python night_demo.py room`.
