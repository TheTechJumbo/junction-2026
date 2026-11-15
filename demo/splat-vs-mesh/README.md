# Splat vs mesh

**Default view: the example living room three ways**, side by side: the room as it was built (the exact
boxes, `home/actual.ply`), the same room as a Gaussian splat (`home/home.splat`) and the block mesh
our code makes from that splat (`home/blocks_*.ply`, with a Quality dropdown), with last night's walk
over the mesh and the night controls under all three. The actual room and the mesh turn together.
Rebuild with `python home.py`. Then `?scene=venue` is the real splat below.


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
- `?scene=room` shows the public Mip-NeRF 360 "room" instead: put `room.splat`
  (huggingface.co/cakewalk/splat-data) in `room/`, run `python room_convert.py room/room.splat room`
  and `python night_demo.py room`.
