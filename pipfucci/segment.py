"""Background correction, nuclear segmentation, quantification, confluence and registration."""

from __future__ import annotations

import numpy as np
from scipy import ndimage as ndi


# ---------------------------------------------------------------- background
def background(img: np.ndarray, nucleus_px: float) -> np.ndarray:
    """Smooth background estimate (rolling-ball-like) from a downsampled minimum filter."""
    step = 4 if min(img.shape) >= 256 else 2
    small = img[::step, ::step]
    size = max(3, int(round(2.5 * nucleus_px / step)) | 1)
    bg = ndi.minimum_filter(small, size=size)
    bg = ndi.uniform_filter(bg, size=size)
    bg = ndi.gaussian_filter(bg, sigma=size / 2.0)
    zoom = (img.shape[0] / bg.shape[0], img.shape[1] / bg.shape[1])
    bg = ndi.zoom(bg, zoom, order=1)
    bg = bg[: img.shape[0], : img.shape[1]]
    if bg.shape != img.shape:  # pad if zoom rounded down
        bg = np.pad(bg, [(0, img.shape[0] - bg.shape[0]), (0, img.shape[1] - bg.shape[1])], mode="edge")
    return bg


def bg_subtract(img: np.ndarray | None, nucleus_px: float) -> np.ndarray | None:
    if img is None:
        return None
    return img - background(img, nucleus_px)


def robust_scale(img: np.ndarray) -> float:
    v = np.percentile(img, 99.5)
    return float(v) if v > 0 else float(img.max() or 1.0)


# ---------------------------------------------------------------- segmentation
def segment_classic(red: np.ndarray | None, green: np.ndarray | None, nucleus_px: float,
                    sensitivity: float = 0.5, scales: tuple[float, float] | None = None) -> np.ndarray:
    """Segment nuclei on the combined red+green image.

    Threshold is set from the background noise (not Otsu) so dim cells at the G1/S and
    S/G2 transitions are kept; touching nuclei are split by seeded watershed.
    """
    from skimage.feature import peak_local_max
    from skimage.segmentation import watershed

    chans = [c for c in (red, green) if c is not None]
    if not chans:
        raise ValueError("Need at least one fluorescence channel to segment nuclei")
    if scales is None:
        scales = tuple(robust_scale(c) for c in chans)
    comb = np.zeros_like(chans[0], dtype=np.float32)
    for c, s in zip(chans, scales):
        comb += np.clip(c, 0, None) / (s if s > 0 else 1.0)

    sig = max(1.0, nucleus_px / 8.0)
    smooth = ndi.gaussian_filter(comb, sig)
    # noise from the lower half of pixels (mostly background)
    med = float(np.median(smooth))
    low = smooth[smooth <= med]
    mad = float(np.median(np.abs(low - med))) * 1.4826 if low.size else 0.0
    mad = max(mad, 1e-4)
    k = 10.0 - 7.0 * float(sensitivity)  # 0.5 -> 6.5 sigma, 1 -> 3 sigma
    thr = med + k * mad
    thr = max(thr, 0.04)  # nuclei are at least 4% of the bright-cell level
    mask = smooth > thr
    mask = ndi.binary_opening(mask, iterations=1)
    mask = ndi.binary_fill_holes(mask)

    area_exp = np.pi * (nucleus_px / 2.0) ** 2
    seed_sig = max(1.0, nucleus_px / 5.0)
    seeds_img = ndi.gaussian_filter(comb, seed_sig)
    lab_mask, nlab = ndi.label(mask)
    if nlab == 0:
        return np.zeros(comb.shape, dtype=np.int32)
    min_d = max(2, int(nucleus_px * 0.45))
    # Seeds: peaks of each channel separately (neighbours are often in different phases,
    # e.g. a green G1 cell touching a red S cell) plus peaks of the distance transform.
    cand = []
    for c, s_ in zip(chans, scales):
        sm = ndi.gaussian_filter(np.clip(c, 0, None) / (s_ if s_ > 0 else 1.0), seed_sig)
        pk = peak_local_max(sm, min_distance=min_d, threshold_abs=thr * 0.5, labels=lab_mask, exclude_border=False)
        cand.append((pk, sm[tuple(pk.T)] if len(pk) else np.zeros(0)))
    dist = ndi.distance_transform_edt(mask)
    pk = peak_local_max(ndi.gaussian_filter(dist, 1.0), min_distance=min_d, threshold_abs=nucleus_px * 0.25,
                        labels=lab_mask, exclude_border=False)
    cand.append((pk, np.full(len(pk), 0.0)))
    coords = _merge_peaks(cand, seeds_img, min_d)
    markers = np.zeros(comb.shape, dtype=np.int32)
    if len(coords):
        markers[tuple(coords.T)] = np.arange(1, len(coords) + 1)
    # components without a peak still get a marker at their maximum
    have = np.zeros(nlab + 1, bool)
    have[lab_mask[tuple(coords.T)]] = True
    missing = np.nonzero(~have[1:])[0] + 1
    if missing.size:
        pos = ndi.maximum_position(seeds_img, lab_mask, missing)
        start = len(coords) + 1
        for i, p in enumerate(pos):
            markers[p] = start + i
    labels = watershed(-seeds_img, markers, mask=mask)
    # remove debris
    sizes = np.bincount(labels.ravel())
    small = sizes < max(4, 0.12 * area_exp)
    small[0] = False
    labels[small[labels]] = 0
    labels = relabel(labels)
    return labels.astype(np.int32)


def _merge_peaks(cand, score_img, min_d):
    """Non-maximum suppression over seed candidates from several sources."""
    pts = [p for p, _ in cand if len(p)]
    if not pts:
        return np.zeros((0, 2), dtype=int)
    allp = np.unique(np.vstack(pts), axis=0)
    score = score_img[tuple(allp.T)]
    order = np.argsort(-score)
    from scipy.spatial import cKDTree

    tree = cKDTree(allp)
    taken = np.zeros(len(allp), bool)
    suppressed = np.zeros(len(allp), bool)
    for i in order:
        if suppressed[i]:
            continue
        taken[i] = True
        for j in tree.query_ball_point(allp[i], r=min_d):
            if j != i:
                suppressed[j] = True
    return allp[taken]


def relabel(labels: np.ndarray) -> np.ndarray:
    """Renumber labels 1..n (0 stays background)."""
    u = np.unique(labels)
    u = u[u > 0]
    lut = np.zeros(int(labels.max()) + 1, dtype=np.int32)
    lut[u] = np.arange(1, len(u) + 1, dtype=np.int32)
    return lut[labels]


_CELLPOSE_MODEL = None


def cellpose_available() -> bool:
    try:
        import importlib.util

        return importlib.util.find_spec("cellpose") is not None
    except Exception:
        return False


def segment_cellpose(red, green, nucleus_px, scales=None):  # pragma: no cover - optional
    global _CELLPOSE_MODEL
    from cellpose import core, models

    chans = [c for c in (red, green) if c is not None]
    if scales is None:
        scales = tuple(robust_scale(c) for c in chans)
    comb = sum(np.clip(c, 0, None) / s for c, s in zip(chans, scales))
    if _CELLPOSE_MODEL is None:
        gpu = False
        try:
            gpu = core.use_gpu()
        except Exception:
            pass
        if hasattr(models, "Cellpose"):
            _CELLPOSE_MODEL = models.Cellpose(gpu=gpu, model_type="nuclei")
        else:
            _CELLPOSE_MODEL = models.CellposeModel(gpu=gpu)
    out = _CELLPOSE_MODEL.eval(comb, diameter=nucleus_px, channels=[0, 0])
    return np.asarray(out[0], dtype=np.int32)


# ---------------------------------------------------------------- quantification
def quantify(labels: np.ndarray, red: np.ndarray | None, green: np.ndarray | None, edge_margin: float) -> dict:
    n = int(labels.max())
    if n == 0:
        return {k: np.zeros(0) for k in ("label", "y", "x", "area", "red", "green", "red_med", "green_med", "edge")}
    idx = np.arange(1, n + 1)
    area = ndi.sum_labels(np.ones_like(labels, dtype=np.float32), labels, idx)
    cy, cx = np.array(ndi.center_of_mass(np.ones_like(labels, dtype=np.float32), labels, idx)).T
    def meanof(img):
        if img is None:
            return np.full(n, np.nan)
        return np.asarray(ndi.mean(img, labels, idx), dtype=np.float32)
    def medof(img):
        if img is None:
            return np.full(n, np.nan)
        return np.asarray(ndi.median(img, labels, idx), dtype=np.float32)
    h, w = labels.shape
    # touches the border or is within the margin
    objs = ndi.find_objects(labels)
    edge = np.zeros(n, bool)
    for i, sl in enumerate(objs):
        if sl is None:
            continue
        if sl[0].start <= 0 or sl[1].start <= 0 or sl[0].stop >= h or sl[1].stop >= w:
            edge[i] = True
    edge |= (cx < edge_margin) | (cy < edge_margin) | (cx > w - 1 - edge_margin) | (cy > h - 1 - edge_margin)
    return {
        "label": idx.astype(np.int32),
        "y": cy.astype(np.float32),
        "x": cx.astype(np.float32),
        "area": area.astype(np.float32),
        "red": meanof(red),
        "green": meanof(green),
        "red_med": medof(red),
        "green_med": medof(green),
        "edge": edge,
    }


# ---------------------------------------------------------------- confluence
def confluence(phase: np.ndarray | None, nucleus_px: float, thr: float | None = None):
    """Fraction of the field covered by cells, from local texture in phase contrast."""
    if phase is None:
        return np.nan, thr
    img = phase[::2, ::2].astype(np.float32)
    win = max(3, int(nucleus_px / 2) | 1)
    m = ndi.uniform_filter(img, win)
    m2 = ndi.uniform_filter(img * img, win)
    sd = np.sqrt(np.clip(m2 - m * m, 0, None))
    sd = ndi.gaussian_filter(sd, win / 2)
    if thr is None:
        from skimage.filters import threshold_otsu

        try:
            thr = float(threshold_otsu(sd))
        except Exception:
            thr = float(np.median(sd))
    return float((sd > thr).mean()), thr


# ---------------------------------------------------------------- registration
def drift(prev: np.ndarray, cur: np.ndarray) -> tuple[float, float]:
    """Shift (dy, dx) of cur relative to prev, from phase cross-correlation on a 2x downsample."""
    from skimage.registration import phase_cross_correlation

    a = prev[::2, ::2]
    b = cur[::2, ::2]
    try:
        shift, _, _ = phase_cross_correlation(a, b, upsample_factor=4, normalization=None)
    except Exception:
        return 0.0, 0.0
    # shift is what moves cur onto prev; drift of content is the negative
    return float(-shift[0] * 2), float(-shift[1] * 2)
