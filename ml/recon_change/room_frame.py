"""The room map's frame, for files that live outside the relay.

locate.py's map frame says where the map's origin is in a pass's 3D frame, which way east, north and
up point there, and how many metres one unit is (map_frame / to_map). Saved as JSON
({"origin", "east", "north", "up", "metres"}), it lets a trained splat or its mesh be moved into the
map's frame (east, north, up in metres: z up, floor near 0), where the relay's fixes, named places
and the captured walk (capture.py) already are, so they all line up in the family's 3D view.
"""
import json

import numpy as np


def load(path):
    f = json.load(open(path))
    if "matrix" in f and "origin" not in f:  # the relay's GET /care/space reply, saved as it came
        return from_matrix(f["matrix"])
    return {k: np.asarray(f[k], float) for k in ("origin", "east", "north", "up")} | {"metres": float(f.get("metres", 1.0))}


def from_matrix(matrix):
    """The relay's 4x4 matrix from a trained splat into the room map (GET /care/space: scale times a
    turn, then a shift) as a frame: map = metres * turn @ (p - origin)."""
    m = np.asarray(matrix, float)
    metres = float(np.cbrt(abs(np.linalg.det(m[:3, :3]))))
    u, _, vt = np.linalg.svd(m[:3, :3] / metres)  # the nearest true turn (the matrix is rounded)
    turn = u @ vt
    return {"origin": -turn.T @ m[:3, 3] / metres, "east": turn[0], "north": turn[1], "up": turn[2], "metres": metres}


def to_room(points, frame):
    """(n, 3) points in the pass's frame -> (n, 3) east, north, up in the map's metres (locate.to_map)."""
    d = (np.atleast_2d(points) - frame["origin"]) * frame["metres"]
    return np.stack([d @ frame["east"], d @ frame["north"], d @ frame["up"]], 1)


def turn(vectors, frame):
    """Directions (normals) into the map's axes: the same turn, no move, no scale."""
    v = np.atleast_2d(vectors)
    return np.stack([v @ frame["east"], v @ frame["north"], v @ frame["up"]], 1)
