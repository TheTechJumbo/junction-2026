//! Rust versions of the per-pixel loops in recon_change.py (JUN-28).
//!
//! Each function does the same arithmetic as the NumPy code, in float32 and in the same order,
//! but per point instead of per array: no temporary arrays, one pass over the points, spread over
//! all CPU cores. The equivalence check in test_rust.py counts any value that comes out different.

use numpy::{
    PyArray1, PyArray3, PyArrayMethods, PyReadonlyArray1, PyReadonlyArray2, PyReadonlyArray3, PyUntypedArrayMethods,
};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use rayon::prelude::*;

/// Points per parallel task. Large enough that task overhead does not matter.
const CHUNK: usize = 4096;

/// a . b for 3-vectors, rounded the way NumPy's float32 matrix product (OpenBLAS) rounds it:
/// a0*b0, then two fused multiply-adds. A float32 FMA is done exactly in float64 (the product of
/// two float32 numbers fits in a float64), which needs no special CPU instructions. Measured on
/// 345,744 values: identical to NumPy's `@`; the plain a0*b0 + a1*b1 + a2*b2 differs in 45% of them.
#[inline(always)]
fn dot3(a: &[f32], b: [f32; 3]) -> f32 {
    let fma = |x: f32, y: f32, acc: f32| (x as f64 * y as f64 + acc as f64) as f32;
    fma(a[2], b[2], fma(a[1], b[1], a[0] * b[0]))
}

/// One camera: world -> camera transform, intrinsics, and its depth, nearest-depth and trust maps.
#[derive(Clone, Copy)]
struct View<'a> {
    e: &'a [f32], // 3 x 4, row major
    k: &'a [f32], // 3 x 3, row major
    depth: &'a [f32],
    near: &'a [f32],
    valid: &'a [bool],
    h: usize,
    w: usize,
}

impl View<'_> {
    /// The pixel a world point lands on in this camera, and its depth there; None when it is
    /// behind the camera or outside the picture.
    #[inline(always)]
    fn pixel(&self, p: &[f32]) -> Option<(usize, f32)> {
        let e = self.e;
        let k = self.k;
        // cam = R @ p + t, the matrix product rounded as NumPy's does it (see dot3)
        let cx = dot3(p, [e[0], e[1], e[2]]) + e[3];
        let cy = dot3(p, [e[4], e[5], e[6]]) + e[7];
        let z = dot3(p, [e[8], e[9], e[10]]) + e[11];
        if !(z > 1e-6_f32) {  // also catches NaN, as NumPy does
            return None;
        }
        let u = (k[0] * cx / z + k[2]).round_ties_even();
        let v = (k[4] * cy / z + k[5]).round_ties_even();
        if !(u >= 0.0 && u < self.w as f32 && v >= 0.0 && v < self.h as f32) {
            return None;
        }
        Some((v as usize * self.w + u as usize, z))
    }

    /// (gap, gap_near) for one world point, NaN where this camera has no trusted view of it.
    #[inline(always)]
    fn gap(&self, p: &[f32]) -> (f32, f32) {
        match self.pixel(p) {
            Some((i, z)) if self.valid[i] => ((self.depth[i] - z) / z, (self.near[i] - z) / z),
            _ => (f32::NAN, f32::NAN),
        }
    }
}

fn points_slice<'a>(points: &'a PyReadonlyArray2<'_, f32>) -> PyResult<&'a [f32]> {
    if points.shape()[1] != 3 {
        return Err(PyValueError::new_err("points must be (N, 3)"));
    }
    points.as_slice().map_err(|_| PyValueError::new_err("points must be C-contiguous"))
}

fn slice<'a, T: numpy::Element, D: numpy::ndarray::Dimension>(
    a: &'a numpy::PyReadonlyArray<'_, T, D>,
    what: &str,
) -> PyResult<&'a [T]> {
    a.as_slice().map_err(|_| PyValueError::new_err(format!("{what} must be C-contiguous")))
}

/// Project world points into one camera and compare with what it saw. Same as
/// recon_change.depth_gap: returns (gap, gap_near), NaN where the camera has no trusted view.
#[pyfunction]
fn depth_gap<'py>(
    py: Python<'py>,
    points: PyReadonlyArray2<'py, f32>,
    e: PyReadonlyArray2<'py, f32>,
    k: PyReadonlyArray2<'py, f32>,
    depth: PyReadonlyArray2<'py, f32>,
    near: PyReadonlyArray2<'py, f32>,
    valid: PyReadonlyArray2<'py, bool>,
) -> PyResult<(Bound<'py, PyArray1<f32>>, Bound<'py, PyArray1<f32>>)> {
    let (h, w) = (depth.shape()[0], depth.shape()[1]);
    if e.shape() != [3, 4] || k.shape() != [3, 3] || near.shape() != [h, w] || valid.shape() != [h, w] {
        return Err(PyValueError::new_err("E must be (3, 4), K (3, 3), depth, near and valid (H, W)"));
    }
    let pts = points_slice(&points)?;
    let view = View {
        e: slice(&e, "E")?,
        k: slice(&k, "K")?,
        depth: slice(&depth, "depth")?,
        near: slice(&near, "near")?,
        valid: slice(&valid, "valid")?,
        h,
        w,
    };
    let n = pts.len() / 3;
    let (gap, gap_near) = py.allow_threads(|| {
        let mut gap = vec![0f32; n];
        let mut gap_near = vec![0f32; n];
        gap.par_chunks_mut(CHUNK)
            .zip(gap_near.par_chunks_mut(CHUNK))
            .zip(pts.par_chunks(CHUNK * 3))
            .for_each(|((g, gn), p)| {
                for (i, q) in p.chunks_exact(3).enumerate() {
                    (g[i], gn[i]) = view.gap(q);
                }
            });
        (gap, gap_near)
    });
    Ok((PyArray1::from_vec(py, gap), PyArray1::from_vec(py, gap_near)))
}

/// Same as recon_change.votes: for each world point, how many of these frames saw the same
/// surface (agree), how many looked straight through its place (free), and whether any had a
/// trusted view (seen). E, K, depth, near and valid hold every frame; `frames` picks the ones to use.
#[pyfunction]
#[allow(clippy::too_many_arguments)]
fn votes<'py>(
    py: Python<'py>,
    points: PyReadonlyArray2<'py, f32>,
    e: PyReadonlyArray3<'py, f32>,
    k: PyReadonlyArray3<'py, f32>,
    depth: PyReadonlyArray3<'py, f32>,
    near: PyReadonlyArray3<'py, f32>,
    valid: PyReadonlyArray3<'py, bool>,
    frames: PyReadonlyArray1<'py, i64>,
    tol_agree: f32,
    tol_free: f32,
) -> PyResult<(Bound<'py, PyArray1<i32>>, Bound<'py, PyArray1<i32>>, Bound<'py, PyArray1<bool>>)> {
    let (s, h, w) = (depth.shape()[0], depth.shape()[1], depth.shape()[2]);
    if e.shape() != [s, 3, 4] || k.shape() != [s, 3, 3] || near.shape() != [s, h, w] || valid.shape() != [s, h, w] {
        return Err(PyValueError::new_err("E must be (S, 3, 4), K (S, 3, 3), depth, near and valid (S, H, W)"));
    }
    let pts = points_slice(&points)?;
    let (e, k) = (slice(&e, "E")?, slice(&k, "K")?);
    let (depth, near, valid) = (slice(&depth, "depth")?, slice(&near, "near")?, slice(&valid, "valid")?);
    let hw = h * w;
    let mut views = Vec::new();
    for &j in slice(&frames, "frames")? {
        if j < 0 || j as usize >= s {
            return Err(PyValueError::new_err(format!("frame {j} out of range")));
        }
        let j = j as usize;
        views.push(View {
            e: &e[j * 12..(j + 1) * 12],
            k: &k[j * 9..(j + 1) * 9],
            depth: &depth[j * hw..(j + 1) * hw],
            near: &near[j * hw..(j + 1) * hw],
            valid: &valid[j * hw..(j + 1) * hw],
            h,
            w,
        });
    }
    let n = pts.len() / 3;
    let (agree, free, seen) = py.allow_threads(|| {
        // One (depth, near) pair per pixel, NaN where the pixel is not trusted: one memory read per
        // lookup instead of three, which is what the loop waits on.
        let mut agree = vec![0i32; n];
        let mut free = vec![0i32; n];
        let mut seen = vec![false; n];
        agree
            .par_chunks_mut(CHUNK)
            .zip(free.par_chunks_mut(CHUNK))
            .zip(seen.par_chunks_mut(CHUNK))
            .zip(pts.par_chunks(CHUNK * 3))
            .for_each(|(((a, f), sn), p)| {
                // Frame by frame over a block of points: neighbouring points land on neighbouring
                // pixels, so each frame's lookups stay in cache.
                for view in &views {
                    for (i, q) in p.chunks_exact(3).enumerate() {
                        let (gap, gap_near) = view.gap(q);
                        a[i] += (gap.abs() < tol_agree) as i32;
                        f[i] += (gap_near > tol_free) as i32;
                        sn[i] |= gap.is_finite();
                    }
                }
            });
        (agree, free, seen)
    });
    Ok((PyArray1::from_vec(py, agree), PyArray1::from_vec(py, free), PyArray1::from_vec(py, seen)))
}

/// Same as recon_change.unproject: depth map (H, W) -> world points (H, W, 3).
#[pyfunction]
fn unproject<'py>(
    py: Python<'py>,
    depth: PyReadonlyArray2<'py, f32>,
    k: PyReadonlyArray2<'py, f32>,
    e: PyReadonlyArray2<'py, f32>,
) -> PyResult<Bound<'py, PyArray3<f32>>> {
    let (h, w) = (depth.shape()[0], depth.shape()[1]);
    if e.shape() != [3, 4] || k.shape() != [3, 3] {
        return Err(PyValueError::new_err("E must be (3, 4) and K (3, 3)"));
    }
    let (d, k, e) = (slice(&depth, "depth")?, slice(&k, "K")?, slice(&e, "E")?);
    let out = py.allow_threads(|| {
        let mut out = vec![0f32; h * w * 3];
        out.par_chunks_mut(w * 3).enumerate().for_each(|(y, row)| {
            let v = y as f32;
            for x in 0..w {
                let z = d[y * w + x];
                // cam = ((u - cx) / fx * z, (v - cy) / fy * z, z), then R^T (cam - t)
                let c = [(x as f32 - k[2]) / k[0] * z - e[3], (v - k[5]) / k[4] * z - e[7], z - e[11]];
                for j in 0..3 {
                    row[x * 3 + j] = dot3(&c, [e[j], e[4 + j], e[8 + j]]);
                }
            }
        });
        out
    });
    PyArray1::from_vec(py, out).reshape([h, w, 3])
}

/// Median trusted depth of every image patch, for recon_change.patch_table: (n, h, w), NaN where
/// fewer than a quarter of the patch's pixels are trusted. Even counts take the mean of the two
/// middle values, as NumPy does.
#[pyfunction]
fn patch_median<'py>(
    py: Python<'py>,
    depth: PyReadonlyArray3<'py, f32>,
    valid: PyReadonlyArray3<'py, bool>,
    frames: PyReadonlyArray1<'py, i64>,
    h: usize,
    w: usize,
) -> PyResult<Bound<'py, PyArray3<f32>>> {
    let (s, hh, ww) = (depth.shape()[0], depth.shape()[1], depth.shape()[2]);
    if valid.shape() != [s, hh, ww] || h == 0 || w == 0 || h > hh || w > ww {
        return Err(PyValueError::new_err("depth and valid must be (S, H, W), and the patch grid no larger"));
    }
    let (d, ok) = (slice(&depth, "depth")?, slice(&valid, "valid")?);
    let frames = slice(&frames, "frames")?.to_vec();
    if frames.iter().any(|&j| j < 0 || j as usize >= s) {
        return Err(PyValueError::new_err("frame out of range"));
    }
    let (ph, pw) = (hh / h, ww / w);
    let need = ph * pw / 4;
    let out = py.allow_threads(|| {
        let mut out = vec![f32::NAN; frames.len() * h * w];
        out.par_chunks_mut(w).enumerate().for_each_init(Vec::new, |buf, (row, cells)| {
            let (f, py_) = (frames[row / h] as usize, row % h);
            let base = f * hh * ww;
            for (px, cell) in cells.iter_mut().enumerate() {
                buf.clear();
                for y in py_ * ph..(py_ + 1) * ph {
                    for x in px * pw..(px + 1) * pw {
                        let i = base + y * ww + x;
                        if ok[i] && d[i].is_finite() {
                            buf.push(d[i]);
                        }
                    }
                }
                if buf.len() < need.max(1) {
                    continue;
                }
                let m = buf.len() / 2;
                let (_, hi, _) = buf.select_nth_unstable_by(m, f32::total_cmp);
                let hi = *hi;
                *cell = if buf.len() % 2 == 1 {
                    hi
                } else {
                    let lo = buf[..m].iter().copied().fold(f32::NEG_INFINITY, f32::max);
                    (lo + hi) / 2.0
                };
            }
        });
        out
    });
    PyArray1::from_vec(py, out).reshape([frames.len(), h, w])
}

#[inline(always)]
fn dist(a: &[f64], a2: f64, b: &[f32], b2: f64) -> f64 {
    // Same formula as recon_change.distances: |a|^2 + |b|^2 - 2 a.b, in float64.
    let ab = a[0] * b[0] as f64 + a[1] * b[1] as f64 + a[2] * b[2] as f64;
    (a2 + b2 - 2.0 * ab).max(0.0).sqrt()
}

fn norms_f32(b: &[f32]) -> Vec<f64> {
    b.chunks_exact(3).map(|p| p.iter().map(|&x| x as f64 * x as f64).sum()).collect()
}

/// For each row of a: the nearest row of b and its distance (first one on a tie), without
/// building the (A, B) distance table. For recon_change.same_place_similarity.
#[pyfunction]
fn nearest<'py>(
    py: Python<'py>,
    a: PyReadonlyArray2<'py, f32>,
    b: PyReadonlyArray2<'py, f32>,
) -> PyResult<(Bound<'py, PyArray1<i64>>, Bound<'py, PyArray1<f64>>)> {
    let (a, b) = (points_slice(&a)?, points_slice(&b)?);
    if b.is_empty() {
        return Err(PyValueError::new_err("b is empty"));
    }
    let (idx, dmin) = py.allow_threads(|| {
        let b2 = norms_f32(b);
        let rows: Vec<(i64, f64)> = a
            .par_chunks(3)
            .map(|p| {
                let pa = [p[0] as f64, p[1] as f64, p[2] as f64];
                let a2 = pa.iter().map(|x| x * x).sum();
                let mut best = (0i64, f64::INFINITY);
                for (j, q) in b.chunks_exact(3).enumerate() {
                    let d = dist(&pa, a2, q, b2[j]);
                    if d < best.1 {
                        best = (j as i64, d);
                    }
                }
                best
            })
            .collect();
        rows.into_iter().unzip::<_, _, Vec<_>, Vec<_>>()
    });
    Ok((PyArray1::from_vec(py, idx), PyArray1::from_vec(py, dmin)))
}

/// For recon_change.has_lookalike: for each flagged patch (centre, its distance z, its feature),
/// in how many frames of the other clip a patch lies within reach * z AND has a feature dot
/// product above min_sim. Checks the distance first, so most dot products are never computed.
#[pyfunction]
#[allow(clippy::too_many_arguments)]
fn lookalike_count<'py>(
    py: Python<'py>,
    centre: PyReadonlyArray2<'py, f64>,
    z: PyReadonlyArray1<'py, f64>,
    reach: f64,
    feat_a: PyReadonlyArray2<'py, f32>,
    pos: PyReadonlyArray2<'py, f32>,
    feat_b: PyReadonlyArray2<'py, f32>,
    frame_b: PyReadonlyArray1<'py, i64>,
    min_sim: f32,
) -> PyResult<Bound<'py, PyArray1<i32>>> {
    let n_a = centre.shape()[0];
    let (n_b, c) = (pos.shape()[0], feat_b.shape()[1]);
    if centre.shape()[1] != 3 || z.shape() != [n_a] || feat_a.shape() != [n_a, c] || frame_b.shape() != [n_b] {
        return Err(PyValueError::new_err("centre (A, 3), z (A), feat_a (A, C), pos (B, 3), feat_b (B, C), frame_b (B)"));
    }
    let (ca, za, fa) = (slice(&centre, "centre")?, slice(&z, "z")?, slice(&feat_a, "feat_a")?);
    let (pb, fb, fr) = (points_slice(&pos)?, slice(&feat_b, "feat_b")?, slice(&frame_b, "frame_b")?);
    let n_frames = fr.iter().copied().max().map_or(0, |m| m.max(0) as usize + 1);
    let counts = py.allow_threads(|| {
        let b2 = norms_f32(pb);
        (0..n_a)
            .into_par_iter()
            .map(|i| {
                let a = &ca[i * 3..i * 3 + 3];
                let a2 = a.iter().map(|x| x * x).sum();
                let limit = reach * za[i];
                let mine = &fa[i * c..(i + 1) * c];
                let mut hit = vec![false; n_frames];
                for j in 0..n_b {
                    let f = fr[j].max(0) as usize;
                    if hit[f] || !(dist(a, a2, &pb[j * 3..j * 3 + 3], b2[j]) < limit) {
                        continue;
                    }
                    let dot: f32 = mine.iter().zip(&fb[j * c..(j + 1) * c]).map(|(x, y)| x * y).sum();
                    if dot > min_sim {
                        hit[f] = true;
                    }
                }
                hit.iter().filter(|&&h| h).count() as i32
            })
            .collect::<Vec<i32>>()
    });
    Ok(PyArray1::from_vec(py, counts))
}

/// Number of threads the parallel loops use (RAYON_NUM_THREADS, else the CPUs this process may use).
#[pyfunction]
fn threads() -> usize {
    rayon::current_num_threads()
}

#[pymodule]
fn recon_fast(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(depth_gap, m)?)?;
    m.add_function(wrap_pyfunction!(votes, m)?)?;
    m.add_function(wrap_pyfunction!(threads, m)?)?;
    m.add_function(wrap_pyfunction!(unproject, m)?)?;
    m.add_function(wrap_pyfunction!(patch_median, m)?)?;
    m.add_function(wrap_pyfunction!(nearest, m)?)?;
    m.add_function(wrap_pyfunction!(lookalike_count, m)?)?;
    Ok(())
}
