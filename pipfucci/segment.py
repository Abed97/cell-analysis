"""Background correction, nuclear segmentation, quantification, confluence and registration."""

from __future__ import annotations

import numpy as np
from scipy import ndimage as ndi


# ---------------------------------------------------------------- background
def _disk(r: int) -> np.ndarray:
    y, x = np.ogrid[-r:r + 1, -r:r + 1]
    return (x * x + y * y) <= r * r


def background(img: np.ndarray, radius_px: float) -> np.ndarray:
    """Top-hat background: grey opening with a disk larger than any nucleus.

    Anything narrower than the disk (nuclei, even big G2 ones) is removed from the estimate;
    broad structure (uneven green autofluorescence, medium, plastic) stays in it and is then
    subtracted. Done on a min-pooled copy for speed, so the estimate never rises into a cell.
    """
    h, w = img.shape
    f = 4 if min(h, w) >= 256 else 2
    H, W = -(-h // f) * f, -(-w // f) * f
    pad = np.pad(img, [(0, H - h), (0, W - w)], mode="edge") if (H, W) != (h, w) else img
    small = pad.reshape(H // f, f, W // f, f).min(axis=(1, 3))
    r = max(2, int(round(radius_px / f)))
    opened = ndi.grey_opening(small, footprint=_disk(r))
    opened = ndi.gaussian_filter(opened, sigma=max(1.0, r / 2.0))  # smooth: no structure at cell scale
    bg = ndi.zoom(opened, (H / opened.shape[0], W / opened.shape[1]), order=1)[:h, :w]
    if bg.shape != img.shape:
        bg = np.pad(bg, [(0, h - bg.shape[0]), (0, w - bg.shape[1])], mode="edge")
    return bg


def bg_subtract(img: np.ndarray | None, radius_px: float) -> np.ndarray | None:
    if img is None:
        return None
    return img - background(img, radius_px)


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
    # and a contrast floor relative to bright cells, so cell-sized autofluorescence (a green glow
    # over the cytoplasm) is not taken for nuclei: 16% at the default sensitivity, 4% at the maximum
    thr = max(thr, 0.04 + 0.24 * (1.0 - float(sensitivity)))
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
def cell_mask(phase: np.ndarray | None, nucleus_px: float, thr: float | None = None, nuclei: np.ndarray | None = None):
    """Area covered by cells (any colour, or none) from phase contrast, at half resolution.

    Local texture finds cell edges and nuclei; closing and hole filling extend that to whole cell
    bodies; the fluorescent nuclei are added in case a cell is very flat. Returns (mask, thr).
    """
    if phase is None:
        if nuclei is None:
            return None, thr
        return ndi.binary_dilation(nuclei[::2, ::2] > 0, iterations=max(1, int(nucleus_px / 4))), thr
    img = phase[::2, ::2].astype(np.float32)
    win = max(3, int(nucleus_px / 3) | 1)
    m = ndi.uniform_filter(img, win)
    m2 = ndi.uniform_filter(img * img, win)
    sd = np.log1p(np.sqrt(np.clip(m2 - m * m, 0, None)))
    if thr is None:
        from skimage.filters import threshold_otsu

        try:
            thr = float(threshold_otsu(sd))
        except Exception:
            thr = float(np.median(sd))
    mask = sd > thr
    r = max(1, int(round(nucleus_px / 4)))
    mask = ndi.binary_closing(mask, structure=_disk(r), iterations=1)
    mask = ndi.binary_fill_holes(mask)
    mask = ndi.binary_opening(mask, structure=_disk(max(1, r // 2)))
    if nuclei is not None:
        mask |= nuclei[::2, ::2][: mask.shape[0], : mask.shape[1]] > 0
    return mask, thr


def confluence(phase: np.ndarray | None, nucleus_px: float, thr: float | None = None, nuclei=None):
    """Fraction of the field covered by cells. Returns (fraction, thr, mask)."""
    mask, thr = cell_mask(phase, nucleus_px, thr, nuclei)
    if mask is None:
        return np.nan, thr, None
    return float(mask.mean()), thr, mask


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
