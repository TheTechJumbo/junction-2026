#!/usr/bin/env python3
"""A triangle mesh from the refined splat map (splat_refine.py), on a CPU, in plain numpy.

The Gaussians of the map are flat discs: a centre, a colour, and an orientation whose thin axis
is the direction the camera looked. For a mesh, each group of cells at one detail level is
dropped into a voxel grid (a voxel is occupied when a disc centre lies in it, and keeps the mean
colour and the mean disc normal), and the boundary of the occupied voxels is turned into
triangles with surface nets: one vertex per grid cube that has occupied and empty corners, at
the mean of the crossing edges, and one quad (two triangles) per grid edge that crosses.

How fine the voxels are follows the cell's detail level: VOXELS_PER_CELL[level] per cell side.
Static cells at HIGH get a fine mesh, cells still refining a coarser one. Movable cells (things
that move) get the coarsest mesh, or none with include_movable=False.

Limits: thin surfaces (a floor seen from above) come out as a thin closed slab, two sheets close
together. Each detail level is meshed on its own grid, so where cells of two levels meet there is
a seam. The colours are the disc colours averaged per voxel, so the mesh is no sharper than the
splat it comes from.

The file is a binary PLY with per-vertex position, normal and colour (MeshLab, Blender and
three.js read it).

    m = splat_refine.Map.load("map.npz")
    write_ply(m, "map_mesh.ply")
"""
import numpy as np

import splat_refine as sr

VOXELS_PER_CELL = (2, 3, 5, 8)  # detail level -> voxels per cell side (0.25 / 8 = 3 cm at HIGH)
MAX_VOXELS = 30_000_000  # a grid bigger than this is coarsened, to stay within memory
CORNERS = np.array([(i, j, k) for i in (0, 1) for j in (0, 1) for k in (0, 1)])
EDGES = [(a, b) for a in range(8) for b in range(a + 1, 8) if np.abs(CORNERS[a] - CORNERS[b]).sum() == 1]  # the 12 cube edges


def disc_normals(rot):
    """Packed .splat quaternions (N, 4 uint8, w x y z) -> unit normals facing the camera that saw them."""
    q = (rot.astype(np.float32) - 128) / 128
    w, x, y, z = (q / (np.linalg.norm(q, axis=1, keepdims=True) + 1e-9)).T
    thin = np.stack([2 * (x * z + w * y), 2 * (y * z - w * x), 1 - 2 * (x * x + y * y)], 1)  # the disc's thin axis
    return -thin  # the thin axis points along the view, away from the camera


def surface_nets(points, rgb, normals, voxel):
    """Points with colours and normals -> (vertices, normals, colours, triangles) around the
    voxels they occupy."""
    if not len(points):
        return np.zeros((0, 3), np.float32), np.zeros((0, 3), np.float32), np.zeros((0, 3), np.uint8), np.zeros((0, 3), np.int64)
    lo = points.min(0) - 2 * voxel
    dims = np.ceil((points.max(0) + 2 * voxel - lo) / voxel).astype(int) + 1
    while np.prod(dims) > MAX_VOXELS:
        voxel *= 1.5
        lo = points.min(0) - 2 * voxel
        dims = np.ceil((points.max(0) + 2 * voxel - lo) / voxel).astype(int) + 1
    g = np.floor((points - lo) / voxel).astype(int)
    flat = np.ravel_multi_index(g.T, dims)
    n = np.bincount(flat, minlength=np.prod(dims)).reshape(dims)
    occ = n > 0
    col = np.stack([np.bincount(flat, rgb[:, c].astype(np.float64), np.prod(dims)) for c in range(3)], -1).reshape(*dims, 3)
    nor = np.stack([np.bincount(flat, normals[:, c], np.prod(dims)) for c in range(3)], -1).reshape(*dims, 3)

    # one vertex per cube (between 8 voxel centres) with both occupied and empty corners
    cube = tuple(d - 1 for d in dims)
    corner = [occ[i:i + cube[0], j:j + cube[1], k:k + cube[2]] for i, j, k in CORNERS]
    total = sum(c.astype(np.int8) for c in corner)
    active = (total > 0) & (total < 8)
    cid = np.full(cube, -1, np.int64)
    cid[active] = np.arange(active.sum())
    # vertex = mean of the midpoints of the cube edges whose ends differ
    pos, cnt = np.zeros((*cube, 3)), np.zeros(cube)
    for a, b in EDGES:
        cross = corner[a] != corner[b]
        pos += cross[..., None] * ((CORNERS[a] + CORNERS[b]) / 2)
        cnt += cross
    where = np.argwhere(active)
    verts = lo + (where + 0.5 + pos[active] / cnt[active][:, None]) * voxel  # voxel centres are at index + 0.5
    # colour and normal: mean over the occupied corners
    c_sum, n_sum, w_sum = np.zeros((len(where), 3)), np.zeros((len(where), 3)), np.zeros(len(where))
    for (i, j, k), c in zip(CORNERS, corner):
        at = tuple((where + [i, j, k]).T)
        c_sum += col[at]
        n_sum += nor[at]
        w_sum += n[at]
    colours = np.clip(c_sum / np.maximum(w_sum, 1)[:, None], 0, 255).astype(np.uint8)
    vnorm = (n_sum / (np.linalg.norm(n_sum, axis=1, keepdims=True) + 1e-9)).astype(np.float32)

    # one quad per voxel-to-voxel edge where occupancy changes, joining the 4 cubes around it
    tris = []
    for axis in range(3):
        a = occ.take(range(dims[axis] - 1), axis)
        b = occ.take(range(1, dims[axis]), axis)
        i, j, k = np.nonzero(a != b)
        inside = a[i, j, k]  # the inside is at the lower end: faces point up the axis
        o = [(1, 2), (2, 0), (0, 1)][axis]  # the two other axes, in the order that makes the quad face +axis
        base = np.stack([i, j, k], 1)
        quad = []
        for d1, d2 in ((0, 0), (1, 0), (1, 1), (0, 1)):  # the 4 cubes, in turn around the edge
            c = base.copy()
            c[:, o[0]] -= 1 - d1
            c[:, o[1]] -= 1 - d2
            quad.append(cid[tuple(c.T)])
        quad = np.stack(quad, 1)
        quad[~inside] = quad[~inside][:, ::-1]  # wind so the face points from inside to outside
        tris += [quad[:, [0, 1, 2]], quad[:, [0, 2, 3]]]
    tris = np.concatenate(tris)
    return verts.astype(np.float32), vnorm, colours, tris[(tris >= 0).all(1)]


def mesh(m, include_movable=True, levels=None):
    """The map as one mesh: each detail level meshed at its own voxel size. Returns (vertices,
    normals, colours, triangles, summary per level)."""
    if not len(m.splats):
        return surface_nets(np.zeros((0, 3)), None, None, 1.0) + ({},)
    at = np.clip(np.searchsorted(m.keys, m.owner), 0, len(m.keys) - 1)
    level, movable = m.level[at], m.movable[at]
    normals = disc_normals(m.splats["rot"])
    parts, summary, offset = [], {}, 0
    groups = [(f"level {L}", (level == L) & ~movable, VOXELS_PER_CELL[L]) for L in range(len(VOXELS_PER_CELL))
              if levels is None or L in levels]
    if include_movable:
        groups.append(("movable", movable, VOXELS_PER_CELL[sr.LOW]))
    for name, sel, per_cell in groups:
        v, nrm, c, t = surface_nets(m.splats["pos"][sel], m.splats["rgba"][sel, :3], normals[sel], m.cell / per_cell)
        parts.append((v, nrm, c, t + offset))
        offset += len(v)
        summary[name] = {"gaussians": int(sel.sum()), "voxel": round(m.cell / per_cell, 4), "vertices": len(v), "triangles": len(t)}
    v, nrm, c, t = (np.concatenate([p[i] for p in parts]) for i in range(4))
    return v, nrm, c, t, summary


def from_splat(data, voxel=0.05, min_alpha=64, drop_largest=0.01, frame=None):
    """A trained splat file (the 32-byte .splat records: video_map.py's trained.splat, splat.py's
    output) -> (vertices, normals, colours, triangles). There are no cells or detail levels in it, so it
    is meshed at one voxel size. Near-transparent Gaussians (alpha below min_alpha) and the largest
    drop_largest share (trained splats keep big faint blobs for the background and stray floaters)
    are left out, so the mesh follows the surfaces. With a room frame (room_frame.py) the splat is moved
    into the room map's frame first (metres, z up), where the captured walk and the named places are."""
    rec = np.frombuffer(bytes(data), sr.RECORD)
    keep = rec["rgba"][:, 3] >= min_alpha
    if drop_largest and keep.sum() > 100:
        size = rec["scale"].max(1)
        keep &= size <= np.quantile(size[keep], 1 - drop_largest)
    rec = rec[keep]
    pos, normals = rec["pos"].astype(np.float64), disc_normals(rec["rot"])
    if frame is not None:
        import room_frame
        pos, normals = room_frame.to_room(pos, frame), room_frame.turn(normals, frame)
    return surface_nets(pos, rec["rgba"][:, :3], normals, voxel)


def write_mesh(v, nrm, c, t, path, comments=()):
    """Write a mesh as a binary PLY with vertex normals and colours (the format the dashboard reads).
    comments go in the header; "up z" tells the dashboard's viewer which way is up (default -y, VGGT's)."""
    vert = np.zeros(len(v), [("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("nx", "<f4"), ("ny", "<f4"), ("nz", "<f4"),
                             ("red", "u1"), ("green", "u1"), ("blue", "u1")])
    vert["x"], vert["y"], vert["z"] = v.T
    vert["nx"], vert["ny"], vert["nz"] = nrm.T
    vert["red"], vert["green"], vert["blue"] = c.T
    face = np.zeros(len(t), [("n", "u1"), ("i", "<i4", 3)])
    face["n"], face["i"] = 3, t
    header = ("ply\nformat binary_little_endian 1.0\n" + "".join(f"comment {x}\n" for x in comments) + f"element vertex {len(v)}\n"
              "property float x\nproperty float y\nproperty float z\nproperty float nx\nproperty float ny\nproperty float nz\n"
              "property uchar red\nproperty uchar green\nproperty uchar blue\n"
              f"element face {len(t)}\nproperty list uchar int vertex_indices\nend_header\n")
    with open(path, "wb") as f:
        f.write(header.encode())
        f.write(vert.tobytes())
        f.write(face.tobytes())


def write_ply(m, path, include_movable=True, comments=()):
    """Write the map's mesh (all detail levels) as a binary PLY. Returns the summary."""
    v, nrm, c, t, summary = mesh(m, include_movable)
    write_mesh(v, nrm, c, t, path, comments)
    summary["total"] = {"vertices": len(v), "triangles": len(t)}
    return summary


if __name__ == "__main__":
    # A trained splat to the dashboard's mesh:
    #   python splat_mesh.py trained.splat home.ply [voxel] [up] [--frame room.json]
    # up is the splat's up axis: "-y" for VGGT's coordinates (the default), "z" for z-up scenes. With
    # --frame the mesh is moved into the room map's frame (up z), so the captured walk lines up with it.
    import sys
    args = [a for a in sys.argv[1:]]
    frame = None
    if "--frame" in args:
        i = args.index("--frame")
        import room_frame
        frame = room_frame.load(args[i + 1])
        del args[i:i + 2]
    src, out = args[0], args[1]
    voxel = float(args[2]) if len(args) > 2 else 0.05
    up = "z" if frame is not None else (args[3] if len(args) > 3 else "-y")
    v, nrm, c, t = from_splat(open(src, "rb").read(), voxel, frame=frame)
    write_mesh(v, nrm, c, t, out, comments=[f"up {up}", f"from {src.split('/')[-1]}"])
    print(out, len(v), "vertices", len(t), "triangles")

