//! Rust versions of the per-pixel loops in recon_change.py (JUN-28).
//!
//! `depth_gap` and `votes` do the same arithmetic as the NumPy code, in float32 and in the same
//! order, but per point instead of per array: no temporary arrays, one pass over the points,
//! spread over all CPU cores. The NumPy matrix product may round the last bit differently, so a
//! point that sits exactly on a pixel boundary or a tolerance can land on the other side; the
//! equivalence check in test_fast.py counts how often that happens.

use numpy::{PyArray1, PyReadonlyArray1, PyReadonlyArray2, PyReadonlyArray3, PyUntypedArrayMethods};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use rayon::prelude::*;

/// Points per parallel task. Large enough that task overhead does not matter.
const CHUNK: usize = 4096;

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
    /// (gap, gap_near) for one world point, NaN where this camera has no trusted view of it.
    #[inline(always)]
    fn gap(&self, p: &[f32]) -> (f32, f32) {
        let e = self.e;
        let k = self.k;
        // cam = R @ p + t
        let cx = p[0] * e[0] + p[1] * e[1] + p[2] * e[2] + e[3];
        let cy = p[0] * e[4] + p[1] * e[5] + p[2] * e[6] + e[7];
        let z = p[0] * e[8] + p[1] * e[9] + p[2] * e[10] + e[11];
        if !(z > 1e-6_f32) {
            return (f32::NAN, f32::NAN);
        }
        let u = (k[0] * cx / z + k[2]).round_ties_even();
        let v = (k[4] * cy / z + k[5]).round_ties_even();
        if !(u >= 0.0 && u < self.w as f32 && v >= 0.0 && v < self.h as f32) {
            return (f32::NAN, f32::NAN);
        }
        let i = v as usize * self.w + u as usize;
        if !self.valid[i] {
            return (f32::NAN, f32::NAN);
        }
        ((self.depth[i] - z) / z, (self.near[i] - z) / z)
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
        let mut agree = vec![0i32; n];
        let mut free = vec![0i32; n];
        let mut seen = vec![false; n];
        agree
            .par_chunks_mut(CHUNK)
            .zip(free.par_chunks_mut(CHUNK))
            .zip(seen.par_chunks_mut(CHUNK))
            .zip(pts.par_chunks(CHUNK * 3))
            .for_each(|(((a, f), sn), p)| {
                for (i, q) in p.chunks_exact(3).enumerate() {
                    for view in &views {
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
    Ok(())
}
