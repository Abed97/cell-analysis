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
def texture(phase: np.ndarray, nucleus_px: float) -> np.ndarray:
    """Local contrast of the phase image at half resolution: the standard deviation of log
    intensity in a small window. Log makes it independent of brightness and exposure, so the same
    threshold works when an image is darker or brighter than the others."""
    img = phase[::2, ::2].astype(np.float32)
    lg = np.log1p(np.clip(img, 0, None))
    win = max(3, int(nucleus_px / 3) | 1)
    m = ndi.uniform_filter(lg, win)
    m2 = ndi.uniform_filter(lg * lg, win)
    sd = np.sqrt(np.clip(m2 - m * m, 0, None))
    return ndi.gaussian_filter(sd, 1.0)


def background_stats(sd: np.ndarray) -> tuple[float, float, float]:
    """Texture of the empty background in this image: (level, spread, share of the image that looks
    like background). The level is the peak of the histogram; the spread comes from its low side,
    which cells never reach, so it does not depend on how many cells there are."""
    v = sd[::2, ::2].ravel()
    lo, hi = np.percentile(v, [0.5, 99.5])
    if not hi > lo:
        return float(lo), 1e-3, 1.0
    hist, edges = np.histogram(v, bins=256, range=(lo, hi))
    hist = ndi.gaussian_filter1d(hist.astype(float), 2)
    i = int(np.argmax(hist))
    level = 0.5 * float(edges[i] + edges[i + 1])
    below = v[v <= level]
    spread = 1.4826 * float(np.median(level - below)) if len(below) > 50 else 0.2 * float(np.std(v))
    spread = max(spread, 0.1 * level, 1e-4)
    share = float(np.mean(v < level + 3 * spread))
    return level, spread, share


def cell_mask(phase: np.ndarray | None, nucleus_px: float, ref=None, nuclei: np.ndarray | None = None,
              sensitivity: float = 0.5, bridge_px: float | None = None, adjust_px: float = 0.0):
    """Area covered by cells (any colour, or none) from phase contrast, at half resolution.

    Texture above the image's own background (level + k x spread) marks cells; closing joins the
    texture of neighbouring cells, holes are filled, and the fluorescent nuclei are added in case a
    cell is very flat. `ref` is the (level, spread) of the field's first image, used when an image
    is so confluent that it has no background left to measure. sensitivity 0..1 (higher finds more),
    bridge_px joins gaps up to that size, adjust_px grows (+) or shrinks (-) the edge.
    Pixel sizes are full resolution. Returns (mask, (level, spread))."""
    if phase is None:
        if nuclei is None:
            return None, ref
        return ndi.binary_dilation(nuclei[::2, ::2] > 0, iterations=max(1, int(nucleus_px / 4))), ref
    sd = texture(phase, nucleus_px)
    level, spread, share = background_stats(sd)
    if ref is not None and (share < 0.2 or level > ref[0] + 4 * ref[1]):
        level, spread = ref  # hardly any background visible: use the first image's
    k = 4.0 + 16.0 * (1.0 - float(np.clip(sensitivity, 0, 1)))  # 0.5 -> 12 x the background spread
    mask = sd > level + k * spread
    r = max(1, int(round((bridge_px if bridge_px else nucleus_px / 2) / 2)))  # half resolution
    p = r + 1  # close on a padded copy so cells touching the image edge keep their edge
    mask = ndi.binary_closing(np.pad(mask, p, mode="edge"), structure=_disk(r), iterations=1)[p:-p, p:-p]
    mask = ndi.binary_fill_holes(mask)
    # the texture window reaches past the cell edge by about half its width: take that back
    win = max(3, int(nucleus_px / 3) | 1)
    mask = ndi.binary_erosion(mask, structure=_disk(max(1, win // 2)), border_value=1)  # not at the image edge
    mask = ndi.binary_opening(mask, structure=_disk(max(1, r // 2)))
    a = int(round(adjust_px / 2))
    if a > 0:
        mask = ndi.binary_dilation(mask, structure=_disk(a))
    elif a < 0:
        mask = ndi.binary_erosion(mask, structure=_disk(-a), border_value=1)
    if nuclei is not None:
        mask |= nuclei[::2, ::2][: mask.shape[0], : mask.shape[1]] > 0
    return mask, (level, spread)


def confluence(phase: np.ndarray | None, nucleus_px: float, ref=None, nuclei=None, **kw):
    """Fraction of the field covered by cells. Returns (fraction, ref, mask)."""
    mask, ref2 = cell_mask(phase, nucleus_px, ref, nuclei, **kw)
    if mask is None:
        return np.nan, ref2, None
    return float(mask.mean()), ref2, mask


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
