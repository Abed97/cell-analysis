"""PIP-FUCCI phase calling.

G1: PIP-mVenus (green) high, Geminin-mCherry (red) low.
S:  green degraded at S entry, red accumulating -> red only / very low green.
G2/M: green re-accumulates while red stays high -> both high.
Raw calls come from two gates on a red-vs-green scatter. Smoothing then enforces the order
G1 -> S -> G2/M within a track (a Viterbi pass), so cells do not flicker between phases.
"""

from __future__ import annotations

import numpy as np

PHASES = ["G1", "S", "G2M"]
RAW = ["G1", "S", "G2M", "DIM"]
CODE = {"G1": 0, "S": 1, "G2M": 2, "DIM": 3}


def normalise(red: np.ndarray, green: np.ndarray) -> tuple[np.ndarray, np.ndarray, dict]:
    def scale(v):
        v = np.asarray(v, dtype=np.float64)
        v = v[np.isfinite(v)]
        s = np.percentile(v, 99) if v.size else 1.0
        return float(s) if s > 0 else 1.0

    sr, sg = scale(red), scale(green)
    r = np.clip(np.nan_to_num(np.asarray(red, dtype=np.float64)) / sr, 0, None)
    g = np.clip(np.nan_to_num(np.asarray(green, dtype=np.float64)) / sg, 0, None)
    return r.astype(np.float32), g.astype(np.float32), {"red": sr, "green": sg}


def auto_gate(values: np.ndarray, floor: float | None = None) -> float:
    from skimage.filters import threshold_otsu

    v = np.asarray(values, dtype=np.float64)
    v = v[np.isfinite(v)]
    v = np.clip(v, 0, 1.5)
    if v.size < 10:
        t = 0.35
    else:
        try:
            # sqrt spreads the dim end where the transitions sit
            t = float(threshold_otsu(np.sqrt(v))) ** 2
        except Exception:
            t = 0.35
    t = float(min(max(t, 0.05), 0.9))
    if floor is not None and np.isfinite(floor):
        t = max(t, float(floor) * 1.1)
    return t


def raw_calls(r: np.ndarray, g: np.ndarray, gate_red: float, gate_green: float) -> np.ndarray:
    hi_g = g >= gate_green
    hi_r = r >= gate_red
    out = np.full(r.shape, 3, dtype=np.int8)
    out[hi_g & ~hi_r] = 0
    out[~hi_g & hi_r] = 1
    out[hi_g & hi_r] = 2
    return out


def emissions(r, g, gate_red, gate_green, k: float = 6.0) -> np.ndarray:
    """Soft membership of each observation in G1 / S / G2M (n, 3)."""
    x = np.asarray(g, dtype=np.float64) / gate_green
    y = np.asarray(r, dtype=np.float64) / gate_red
    hg = 1.0 / (1.0 + np.exp(-k * np.log(np.clip(x, 1e-3, None)) * 2))
    hr = 1.0 / (1.0 + np.exp(-k * np.log(np.clip(y, 1e-3, None)) * 2))
    pg1 = hg * (1 - hr)
    ps = (1 - hg) * hr
    pg2 = hg * hr
    pdim = (1 - hg) * (1 - hr)
    e = np.stack([pg1 + 0.5 * pdim, ps + 0.45 * pdim, pg2 + 0.05 * pdim], axis=-1) + 1e-3
    return e / e.sum(axis=-1, keepdims=True)


def smooth_tracks(track_index: np.ndarray, frame: np.ndarray, emis: np.ndarray, interval_h: float,
                  window: int = 1) -> np.ndarray:
    """Viterbi per track with order constraints. Returns smoothed state (0,1,2) per detection.

    track_index: dense 0..T-1 track index per detection; frames sorted within a track.
    """
    n = len(track_index)
    out = np.zeros(n, dtype=np.int8)
    if n == 0:
        return out
    order = np.lexsort((frame, track_index))
    ti = track_index[order]
    T = int(ti.max()) + 1
    counts = np.bincount(ti, minlength=T)
    L = int(counts.max())
    starts = np.concatenate([[0], np.cumsum(counts)[:-1]])
    pos = np.arange(n) - starts[ti]
    E = np.ones((T, L, 3))  # neutral beyond track end
    E[ti, pos] = emis[order]
    valid = np.zeros((T, L), bool)
    valid[ti, pos] = True

    if window > 1:
        # average soft memberships over a window (in frames) within each track
        k = np.ones(window) / window
        from scipy.ndimage import convolve1d

        num = convolve1d(np.where(valid[..., None], E, 0.0), k, axis=1, mode="constant")
        den = convolve1d(valid.astype(float), k, axis=1, mode="constant")[..., None]
        E = np.where(valid[..., None], num / np.clip(den, 1e-9, None), 1.0)

    # transition probabilities per frame from typical phase lengths (hours)
    dwell = np.array([10.0, 8.0, 5.0])
    p_leave = 1 - np.exp(-interval_h / dwell)
    A = np.full((3, 3), 1e-9)
    A[0, 0], A[0, 1] = 1 - p_leave[0], p_leave[0]
    A[1, 1], A[1, 2] = 1 - p_leave[1], p_leave[1]
    A[2, 2] = 1 - p_leave[2]
    A[2, 0] = p_leave[2] * 0.2  # a missed division inside one track
    A[0, 2] = 1e-6  # skipping S entirely is very unlikely
    logA = np.log(A / A.sum(axis=1, keepdims=True))
    logE = np.log(E)
    prior = np.log(np.array([0.5, 0.3, 0.2]))

    delta = prior + logE[:, 0]
    back = np.zeros((T, L, 3), dtype=np.int8)
    eye = np.where(np.eye(3) > 0, 0.0, -1e9)
    for t in range(1, L):
        cand = delta[:, :, None] + np.where(valid[:, t, None, None], logA[None], eye[None])
        back[:, t] = np.argmax(cand, axis=1)
        delta = np.max(cand, axis=1) + np.where(valid[:, t, None], logE[:, t], 0.0)
    states = np.zeros((T, L), dtype=np.int8)
    states[:, L - 1] = np.argmax(delta, axis=1)
    for t in range(L - 1, 0, -1):
        states[:, t - 1] = back[np.arange(T), t, states[:, t]]
    out[order] = states[ti, pos]
    return out
