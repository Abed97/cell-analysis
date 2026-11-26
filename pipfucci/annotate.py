"""Annotated images for papers and talks: the viewer's picture at full resolution with nucleus
outlines, cell numbers, confluence area, scale bar and time, as PNG, TIFF or SVG.

Raster formats keep the camera pixels (enlarged 2x or 4x by repeating pixels, never smoothed) and
draw the annotations at the output size, so lines and text stay sharp when the image is shrunk
onto a page. SVG embeds the unchanged image and draws every annotation as vectors (smooth nucleus
and confluence outlines, real text), for Illustrator, Inkscape or Affinity.
"""
from __future__ import annotations

import base64
import io
import math
import re
from pathlib import Path

import numpy as np
from scipy import ndimage as ndi

from .render import PHASE_RGB, composite, live_cell_mask, masks_for

TEXT_RGB = {0: (95, 212, 135), 1: (240, 124, 153), 2: (242, 201, 76), 3: (180, 186, 194)}  # as in the viewer
CONF_RGB = (89, 191, 255)
DEBRIS_RGB = (255, 140, 38)
PHASE_NAMES = {0: "G1", 1: "S", 2: "G2/M"}
BAR_STEPS = (5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000)
TEXT_SCALE = {"small": 0.75, "medium": 1.0, "large": 1.35}


def _hex(rgb) -> str:
    return "#%02X%02X%02X" % tuple(int(v) for v in rgb)


def bar_um(width_px: float, pixel_um: float) -> int:
    """A round scale-bar length near a sixth of the image width."""
    target = width_px * pixel_um / 6
    return min(BAR_STEPS, key=lambda v: abs(math.log(v / target)))


def fmt_hours(h: float) -> str:
    return f"{h:.0f} h" if abs(h - round(h)) < 0.05 else f"{h:.1f} h"


# ------------------------------------------------------------ what to draw
def parts(exp, well: str, field: int, frame: int, layers: set, numbers: bool = False,
          select_track=None, crop=None) -> dict:
    """Everything one annotated image needs, in image pixels. crop: (x0, y0, x1, y1) or None;
    select_track: a track key or a set of them (a cell and its daughters), ringed in white."""
    family = {select_track} if isinstance(select_track, str) else set(select_track or ())
    base = composite(exp, well, field, frame, layers - {"masks", "confluence"}, None, max_width=10 ** 6)
    h, w = base.shape[:2]
    x0, y0, x1, y1 = crop if crop else (0, 0, w, h)
    x0, y0, x1, y1 = max(0, int(x0)), max(0, int(y0)), min(w, int(x1)), min(h, int(y1))
    out = {"base": base[y0:y1, x0:x1], "offset": (x0, y0), "full_shape": (h, w), "labels": None, "lut": None,
           "conf": None, "debris": None, "numbers": [], "sel": None}
    c = exp.cells
    sub = c[(c["well"] == well) & (c["field"] == field) & (c["frame"] == frame)] if c is not None else None
    if ("masks" in layers or family) and sub is not None:
        m = masks_for(exp, well, field)
        lab = m.labels(frame) if m is not None else None
        if lab is not None:
            lab = lab.astype(np.int32)[y0:y1, x0:x1]
            real = sub[~sub["interp"].values.astype(bool)] if "interp" in sub else sub
            col = "call" if "call" in real else "phase"
            top = int(lab.max()) if lab.size else 0
            lut = np.zeros((top + 1, 3), np.uint8)
            phase = np.full(top + 1, -1, np.int16)
            ex = exp.excluded_tracks()
            gone = exp.removed_rows(real)
            sel = np.zeros(top + 1, bool)
            for lbl, ph, trk, rm in zip(real["label"].values, real[col].values, real["track"].values, gone):
                if 0 < lbl <= top:
                    p = 3 if trk in ex or rm else int(ph)
                    lut[lbl], phase[lbl] = PHASE_RGB[p], p
                    sel[lbl] = trk in family
            out["labels"], out["lut"], out["phase"] = lab, lut, phase
            out["show_outlines"] = "masks" in layers
            out["sel"] = sel if sel.any() else None
    if "confluence" in layers and exp.has_results:
        cm = live_cell_mask(exp, well, field, frame, None)
        if cm is not None:
            from .pipeline import drop_patches, object_filter, patch_points

            st = exp.settings
            lab, _, keep = object_filter(cm.astype(bool), st.min_object_um2 / (2 * st.pixel_um) ** 2)
            keep = drop_patches(lab, keep, patch_points(exp.patch_removals(f"{well}_{int(field)}"), frame))
            kept, debris = keep[lab], (lab > 0) & ~keep[lab]
            # half resolution: crop in half pixels, keep the sub-pixel offset
            out["conf"], out["debris"] = kept, debris
    if numbers and sub is not None:
        col = "call" if "call" in sub else "phase"
        ex = exp.excluded_tracks()
        gone = exp.removed_rows(sub)
        for t, x, y, ph, trk, rm in zip(sub["tid"].values, sub["x"].values, sub["y"].values, sub[col].values,
                                        sub["track"].values, gone):
            if x0 <= x < x1 and y0 <= y < y1:
                p = 3 if trk in ex or rm else int(ph)
                out["numbers"].append((float(x) - x0, float(y) - y0, int(t), p, trk in family))
    return out


def _conf_full(mask_half: np.ndarray, full_shape, offset, size, scale: int) -> np.ndarray:
    """Half-resolution mask at the output size, smoothed (bilinear) rather than blocky."""
    h, w = full_shape
    x0, y0 = offset
    ch, cw = size
    f = 2 * scale
    # the half-resolution window covering the crop, with one pixel of margin
    hx0, hy0 = max(0, x0 // 2 - 1), max(0, y0 // 2 - 1)
    hx1, hy1 = min(mask_half.shape[1], (x0 + cw) // 2 + 2), min(mask_half.shape[0], (y0 + ch) // 2 + 2)
    win = mask_half[hy0:hy1, hx0:hx1].astype(np.float32)
    big = ndi.zoom(win, f, order=1, grid_mode=True, mode="nearest") > 0.5
    oy, ox = (y0 - 2 * hy0) * scale, (x0 - 2 * hx0) * scale
    big = big[oy:oy + ch * scale, ox:ox + cw * scale]
    if big.shape != (ch * scale, cw * scale):
        big = np.pad(big, [(0, ch * scale - big.shape[0]), (0, cw * scale - big.shape[1])])
    return big


# ------------------------------------------------------------ raster (PNG / TIFF)
FONT_FILES = ("/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/Supplemental/Arial.ttf",
              "/Library/Fonts/Arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/dejavu/DejaVuSans.ttf")
_FONTS: dict = {}


def _font(size: float):
    """A font that has µ (Pillow's built-in one does not): Helvetica on a Mac, as in the SVG."""
    from PIL import ImageFont

    n = max(6, int(round(size)))
    if n not in _FONTS:
        for f in FONT_FILES:
            try:
                _FONTS[n] = ImageFont.truetype(f, n)
                break
            except Exception:
                continue
        else:
            try:
                _FONTS[n] = ImageFont.load_default(size=n)
            except TypeError:  # Pillow < 10.1
                _FONTS[n] = ImageFont.load_default()
    return _FONTS[n]


def _unit(cw: int, ch: int, opts: dict) -> float:
    """Text height in camera pixels: about 2.7% of the shorter side, at least 7 px."""
    return TEXT_SCALE.get(opts.get("text", "medium"), 1.0) * max(7.0, 0.027 * min(cw, ch))


def raster(p: dict, scale: int, pixel_um: float, opts: dict):
    """PIL image at `scale` times the camera pixels with the annotations drawn at that size."""
    from PIL import Image, ImageDraw

    s = max(1, int(scale))
    base = p["base"]
    ch, cw = base.shape[:2]
    img = np.repeat(np.repeat(base, s, axis=0), s, axis=1) if s > 1 else base.copy()
    lw = max(1, int(round(s * 1.0)))  # outline width, about one camera pixel

    if p["conf"] is not None:
        conf = _conf_full(p["conf"], p["full_shape"], p["offset"], (ch, cw), s)
        deb = _conf_full(p["debris"], p["full_shape"], p["offset"], (ch, cw), s)
        tint = np.array(CONF_RGB, np.float32)
        f = img.astype(np.float32)
        f[conf] = f[conf] * 0.7 + tint * 0.3
        e = max(1, int(round(2 * s)))
        edge = conf & ~ndi.binary_erosion(conf, iterations=e)
        f[edge] = tint
        if deb.any():
            dedge = deb & ~ndi.binary_erosion(deb, iterations=e)
            f[dedge] = DEBRIS_RGB
        img = f.astype(np.uint8)

    if p["labels"] is not None and (p.get("show_outlines") or p["sel"] is not None):
        lab = p["labels"]
        big = np.repeat(np.repeat(lab, s, axis=0), s, axis=1) if s > 1 else lab
        if p.get("show_outlines"):
            er = ndi.grey_erosion(big, size=2 * lw + 1)
            edge = (big != er) & (big > 0)
            img[edge] = p["lut"][big[edge]]
        if p["sel"] is not None:
            sel = p["sel"][big]
            ring = ndi.binary_dilation(sel, iterations=3 * lw) & ~ndi.binary_dilation(sel, iterations=lw)
            img[ring] = 255

    im = Image.fromarray(img)
    d = ImageDraw.Draw(im)
    W, H = im.size
    unit = s * _unit(cw, ch, opts)
    pad = 0.9 * unit
    stroke = max(1, int(round(unit / 9)))

    def text(xy, msg, fill, size, anchor):
        d.text(xy, msg, fill=fill, font=_font(size), anchor=anchor, stroke_width=max(1, int(round(size / 9))),
               stroke_fill=(0, 0, 0))

    if p["numbers"]:
        nfs = 0.8 * unit
        npx = opts.get("nucleus_px", 20) * s
        for x, y, t, ph, sel in p["numbers"]:
            text((x * s, y * s - 0.62 * npx), str(t), (255, 255, 255) if sel else TEXT_RGB[ph], nfs * (1.25 if sel else 1), "ms")
    if opts.get("time"):
        text((pad, pad), opts["time"], (255, 255, 255), unit, "lt")
    if opts.get("label"):
        text((W - pad, pad), opts["label"], (255, 255, 255), unit, "rt")
    if opts.get("bar", True) and pixel_um > 0:
        um = bar_um(cw, pixel_um)
        bl = um / pixel_um * s
        bh = max(2, int(round(0.28 * unit)))
        x0b, y1b = pad, H - pad
        d.rectangle([x0b - stroke, y1b - bh - stroke, x0b + bl + stroke, y1b + stroke], fill=(0, 0, 0))
        d.rectangle([x0b, y1b - bh, x0b + bl, y1b], fill=(255, 255, 255))
        if opts.get("bar_text", True):
            text((x0b + bl / 2, y1b - bh - 0.35 * unit), f"{um} µm", (255, 255, 255), unit, "ms")
    if opts.get("legend"):
        x = W - pad
        for ph in (2, 1, 0):
            name = PHASE_NAMES[ph]
            text((x, H - pad), name, TEXT_RGB[ph], unit, "rs")
            x -= d.textlength(name, font=_font(unit)) + 0.7 * unit
    return im


# ------------------------------------------------------------ vector (SVG)
def _paths(mask: np.ndarray, factor: float, offset=(0.0, 0.0)) -> str:
    """SVG path data around the regions of a boolean mask (sub-pixel contours)."""
    from skimage import measure

    if not mask.any():
        return ""
    # repeat the border pixels outwards: a region cut by the window edge closes outside it, where
    # the clip path hides the closing line
    k = 4  # 3 repeated pixels, then 1 empty one so every contour is closed
    padded = np.pad(np.pad(mask.astype(np.float32), k - 1, mode="edge"), 1)
    out = []
    for cnt in measure.find_contours(padded, 0.5):
        if len(cnt) < 3:
            continue
        cnt = cnt[:: max(1, len(cnt) // 400)]
        pts = " ".join(f"{(c[1] - k + 0.5) * factor + offset[0]:.1f},{(c[0] - k + 0.5) * factor + offset[1]:.1f}" for c in cnt)
        out.append(f"M{pts}Z")
    return "".join(out)


def svg(p: dict, pixel_um: float, opts: dict) -> str:
    """SVG in camera pixels: the image embedded unchanged plus vector annotations."""
    from PIL import Image

    base = p["base"]
    ch, cw = base.shape[:2]
    buf = io.BytesIO()
    Image.fromarray(base).save(buf, format="PNG", optimize=True)
    href = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    unit = _unit(cw, ch, opts)
    pad = 0.9 * unit
    x0, y0 = p["offset"]
    cid = f"clip{opts.get('uid', 0)}"
    clip = f'clip-path="url(#{cid})"'
    parts_out = [f'<defs><clipPath id="{cid}"><rect x="0" y="0" width="{cw}" height="{ch}"/></clipPath></defs>',
                 f'<image x="0" y="0" width="{cw}" height="{ch}" preserveAspectRatio="none" '
                 f'style="image-rendering:pixelated" xlink:href="{href}" href="{href}"/>']

    if p["conf"] is not None:
        hx0, hy0 = x0 / 2.0, y0 / 2.0
        hy1, hx1 = (y0 + ch) / 2.0, (x0 + cw) / 2.0
        win = (slice(max(0, int(hy0) - 1), min(p["conf"].shape[0], int(math.ceil(hy1)) + 1)),
               slice(max(0, int(hx0) - 1), min(p["conf"].shape[1], int(math.ceil(hx1)) + 1)))
        off = (win[1].start * 2 - x0, win[0].start * 2 - y0)
        dk = _paths(p["conf"][win], 2.0, off)
        if dk:
            parts_out.append(f'<g id="confluence" {clip}><path d="{dk}" fill="{_hex(CONF_RGB)}" fill-opacity="0.3" '
                             f'fill-rule="evenodd" stroke="{_hex(CONF_RGB)}" stroke-width="2" stroke-linejoin="round"/></g>')
        dd = _paths(p["debris"][win], 2.0, off)
        if dd:
            parts_out.append(f'<g id="not-counted" {clip}><path d="{dd}" fill="none" fill-rule="evenodd" '
                             f'stroke="{_hex(DEBRIS_RGB)}" stroke-width="2" stroke-linejoin="round"/></g>')

    if p["labels"] is not None and (p.get("show_outlines") or p["sel"] is not None):
        lab = p["labels"]
        objs = ndi.find_objects(lab)
        by_phase: dict = {}
        sel_d = []
        for i, sl in enumerate(objs, start=1):
            if sl is None or i >= len(p["lut"]):
                continue
            sl2 = (slice(max(0, sl[0].start - 1), sl[0].stop + 1), slice(max(0, sl[1].start - 1), sl[1].stop + 1))
            d = _paths(lab[sl2] == i, 1.0, (sl2[1].start, sl2[0].start))
            if not d:
                continue
            if p.get("show_outlines") and p["phase"][i] >= 0:
                by_phase.setdefault(int(p["phase"][i]), []).append(d)
            if p["sel"] is not None and p["sel"][i]:
                sel_d.append(d)
        if sel_d:  # a white halo under the cell's own outline
            parts_out.append(f'<g id="selected-cell" {clip}><path d="{"".join(sel_d)}" fill="none" stroke="#FFFFFF" '
                             f'stroke-width="{4.5 if by_phase else 2.5}" stroke-linejoin="round"/></g>')
        if by_phase:
            g = "".join(f'<path d="{"".join(ds)}" fill="none" stroke="{_hex(PHASE_RGB[ph])}" stroke-width="1.2" '
                        f'stroke-linejoin="round"/>' for ph, ds in sorted(by_phase.items()))
            parts_out.append(f'<g id="nucleus-outlines" {clip}>{g}</g>')

    def text(x, y, msg, rgb, size, anchor="start", baseline="auto"):
        msg = msg.replace("&", "&amp;").replace("<", "&lt;")
        return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="Helvetica, Arial, sans-serif" font-size="{size:.1f}" '
                f'text-anchor="{anchor}" dominant-baseline="{baseline}" fill="{_hex(rgb)}" stroke="#000000" '
                f'stroke-width="{size / 9:.2f}" paint-order="stroke" stroke-linejoin="round">{msg}</text>')

    if p["numbers"]:
        npx = opts.get("nucleus_px", 20)
        nfs = 0.8 * unit
        t = "".join(text(x, y - 0.62 * npx, str(n), (255, 255, 255) if sel else TEXT_RGB[ph], nfs * (1.25 if sel else 1), "middle")
                    for x, y, n, ph, sel in p["numbers"])
        parts_out.append(f'<g id="cell-numbers">{t}</g>')
    if opts.get("time"):
        parts_out.append(f'<g id="time">{text(pad, pad + 0.8 * unit, opts["time"], (255, 255, 255), unit)}</g>')
    if opts.get("label"):
        parts_out.append(f'<g id="label">{text(cw - pad, pad + 0.8 * unit, opts["label"], (255, 255, 255), unit, "end")}</g>')
    if opts.get("bar", True) and pixel_um > 0:
        um = bar_um(cw, pixel_um)
        bl = um / pixel_um
        bh = max(2.0, 0.28 * unit)
        y1 = ch - pad
        g = (f'<rect x="{pad:.1f}" y="{y1 - bh:.1f}" width="{bl:.1f}" height="{bh:.1f}" fill="#FFFFFF" '
             f'stroke="#000000" stroke-width="{max(1.0, unit / 9):.2f}" paint-order="stroke"/>')
        if opts.get("bar_text", True):
            g += text(pad + bl / 2, y1 - bh - 0.35 * unit, f"{um} µm", (255, 255, 255), unit, "middle")
        parts_out.append(f'<g id="scale-bar">{g}</g>')
    if opts.get("legend"):
        x, items = cw - pad, []
        for ph in (2, 1, 0):
            items.append(text(x, ch - pad, PHASE_NAMES[ph], TEXT_RGB[ph], unit, "end"))
            x -= (len(PHASE_NAMES[ph]) * 0.62 + 0.7) * unit
        parts_out.append(f'<g id="legend">{"".join(items)}</g>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            f'width="{cw}" height="{ch}" viewBox="0 0 {cw} {ch}">' + "".join(parts_out) + "</svg>")


# ------------------------------------------------------------ export
def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9 _().,+@-]+", "_", name).strip() or "image"


def export(exp, well: str, field: int, frames: list[int], layers: set, fmt: str = "png", scale: int = 2,
           numbers: bool = False, select_track: str | None = None, crop_um: float | None = None,
           strip: bool = False, label: str = "field", time: bool = True, bar: bool = True,
           legend: bool = False, text: str = "medium") -> dict:
    """Write annotated images of one field at the given frames into the experiment's results
    folder (Figures/Images). crop_um: a square of that size around the selected cell, following it."""
    import time as _time

    from PIL import Image

    fmt = fmt if fmt in ("png", "tif", "svg") else "png"
    scale = 1 if fmt == "svg" else max(1, min(4, int(scale)))
    st = exp.settings
    hours = list(exp.frame_hours())
    frames = [int(f) for f in frames][:48]
    if not frames:
        raise ValueError("Choose at least one time point.")
    cond = ""
    if label == "condition":
        cid = st.wells.get(well)
        cond = next((c["name"] for c in st.conditions if c["id"] == cid), well)
    stamp = _time.strftime("%Y-%m-%d %H%M%S")
    root = exp.results_dir / "figures" / "Images"
    out, n = root / _safe(f"{well} field {field} {stamp}"), 2
    while out.exists():
        out, n = root / _safe(f"{well} field {field} {stamp} ({n})"), n + 1
    out.mkdir(parents=True)
    family = set()
    if select_track:  # the cell and everything it divides into
        family, todo = {select_track}, [select_track]
        t = exp.tracks
        if t is not None and "parent" in t:
            kids: dict = {}
            for k, par in zip(t.index, t["parent"].values):
                if par:
                    kids.setdefault(par, []).append(k)
            while todo:
                for k in kids.get(todo.pop(), []):
                    if k not in family:
                        family.add(k)
                        todo.append(k)
    tr = None
    if crop_um and family:
        c = exp.cells
        tr = c[c["track"].isin(family)][["frame", "x", "y"]]
        if not len(tr):
            tr = None
    written, pieces = [], []
    side = int(round(crop_um / st.pixel_um)) if crop_um else 0
    for n_img, f in enumerate(frames):
        crop = None
        if tr is not None:
            fr = tr["frame"].values
            near = fr[int(np.argmin(np.abs(fr - f)))]
            at = tr[fr == near]  # the cell, or its daughters together, at the nearest time it is seen
            cx, cy = float(at["x"].mean()), float(at["y"].mean())
            hgt, wid = (exp.meta["movies"][f"{well}_{field}"].get("shape") or (0, 0))[:2]
            x0, y0 = int(round(cx - side / 2)), int(round(cy - side / 2))
            if wid:  # keep the square inside the image
                x0 = min(max(0, x0), max(0, wid - side))
                y0 = min(max(0, y0), max(0, hgt - side))
            crop = (x0, y0, x0 + side, y0 + side)
        p = parts(exp, well, field, f, set(layers), numbers, family, crop)
        h = hours[f] if f < len(hours) else 0.0
        opts = {"time": f"t = {fmt_hours(h)}" if time else "", "bar": bar and (not strip or n_img == 0),
                "legend": legend and (not strip or n_img == len(frames) - 1), "text": text,
                "label": (f"{well} · field {field}" if label == "field" else cond if label == "condition" else ""),
                "nucleus_px": st.nucleus_px, "uid": n_img}
        name = _safe(f"{well} field {field} {fmt_hours(h)}")
        if fmt == "svg":
            doc = svg(p, st.pixel_um, opts)
            path = out / f"{name}.svg"
            path.write_text('<?xml version="1.0" encoding="UTF-8"?>\n' + doc, encoding="utf-8")
            pieces.append((doc, p["base"].shape[1], p["base"].shape[0]))
        else:
            im = raster(p, scale, st.pixel_um, opts)
            dpi = 300
            path = out / f"{name}.{fmt}"
            if fmt == "tif":
                im.save(path, format="TIFF", compression="tiff_lzw", dpi=(dpi, dpi))
            else:
                im.save(path, format="PNG", dpi=(dpi, dpi), optimize=False)
            pieces.append(im)
        written.append(path)
    if strip and len(pieces) > 1:
        gap = 12 * scale
        if fmt == "svg":
            x, body = 0, []
            hmax = max(hh for _, _, hh in pieces)
            for doc, ww, hh in pieces:
                inner = doc.split(">", 1)[1].rsplit("</svg>", 1)[0]
                body.append(f'<g transform="translate({x},0)">{inner}</g>')
                x += ww + 12
            doc = (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
                   f'width="{x - 12}" height="{hmax}" viewBox="0 0 {x - 12} {hmax}">' + "".join(body) + "</svg>")
            path = out / "Time strip.svg"
            path.write_text('<?xml version="1.0" encoding="UTF-8"?>\n' + doc, encoding="utf-8")
        else:
            wsum = sum(im.size[0] for im in pieces) + gap * (len(pieces) - 1)
            hmax = max(im.size[1] for im in pieces)
            canvas = Image.new("RGB", (wsum, hmax), (255, 255, 255))
            x = 0
            for im in pieces:
                canvas.paste(im, (x, 0))
                x += im.size[0] + gap
            path = out / f"Time strip.{fmt}"
            if fmt == "tif":
                canvas.save(path, format="TIFF", compression="tiff_lzw", dpi=(300, 300))
            else:
                canvas.save(path, format="PNG", dpi=(300, 300))
        written.append(path)
    first = pieces[0]
    size = (first[1], first[2]) if fmt == "svg" else first.size
    return {"folder": str(out), "paths": [str(p) for p in written], "size": list(size)}


# ------------------------------------------------------------ one cell over time (montage)
MONT_ROWS = ("red", "green", "phase", "merge")
MONT_NAMES = {"red": "mCherry-Geminin", "green": "PIP-mVenus", "phase": "Phase", "merge": "Merge"}
MONT_NAME_RGB = {"red": (200, 40, 90), "green": (40, 140, 60), "phase": (0, 0, 0), "merge": (0, 0, 0)}
CALL_NAMES = {0: "G1", 1: "S", 2: "G2/M", 3: "Colourless"}
CALL_RGB = {0: (46, 139, 62), 1: (208, 47, 53), 2: (179, 134, 0), 3: (120, 120, 120)}  # readable on white


def track_montage(exp, key: str, n: int = 8, rows=MONT_ROWS, fmt: str = "png", scale: int = 4,
                  calls: bool = True, times: bool = True, title: bool = True, bar: bool = True,
                  text: str = "medium") -> dict:
    """The track card's image montage for a paper: one column per time point along the cell, one
    row per channel, with the time above and the phase call below each column, on white.

    PNG and TIFF repeat the camera pixels (never smoothed) and draw the text at the output size,
    300 dpi; SVG embeds the same tiles with real text, for Illustrator, Inkscape or Affinity."""
    import time as _time

    from PIL import Image, ImageDraw

    from .render import montage_tiles

    if exp.tracks is None or key not in exp.tracks.index:
        raise ValueError("No such cell.")
    fmt = fmt if fmt in ("png", "tif", "svg") else "png"
    s = max(1, min(8, int(scale)))
    rows = [r for r in MONT_ROWS if r in set(rows or [])] or list(MONT_ROWS)
    cols, side = montage_tiles(exp, key, max(1, min(24, int(n))))
    if not cols:
        raise ValueError("This cell has no images.")
    st = exp.settings
    hours = list(exp.frame_hours())
    t = exp.tracks.loc[key]
    tile = side * s
    fs = TEXT_SCALE.get(text, 1.0) * max(11.0, 0.17 * tile)
    pad = round(0.6 * fs)
    gap = max(2, round(0.04 * tile))
    f_lab = _font(fs)
    meas = ImageDraw.Draw(Image.new("RGB", (4, 4)))
    tw = lambda msg, size=fs: meas.textlength(msg, font=_font(size))  # noqa: E731
    lab_w = round(max(tw(MONT_NAMES[r]) for r in rows) + pad)
    name = f"{t['well']}·{int(t['field'])}·{int(t['tid'])}" if "tid" in t else key
    cond = (st.condition_of(t["well"]) or {}).get("name", "") if hasattr(st, "condition_of") else ""
    head_txt = f"Track {name}" + (f" · {cond}" if cond else "")
    title_h = round(1.6 * fs) if title else 0
    time_h = round(1.5 * fs) if times else 0
    call_h = round(1.5 * fs) if calls else 0
    um = bar_um(side * 1.5, st.pixel_um) if bar and st.pixel_um > 0 else 0
    foot_h = round(1.5 * fs) if um else 0
    gx0, gy0 = pad + lab_w, pad + title_h + time_h
    gw = len(cols) * tile + (len(cols) - 1) * gap
    gh = len(rows) * tile + (len(rows) - 1) * gap
    W, H = gx0 + gw + pad, gy0 + gh + call_h + foot_h + pad
    # the call names have to fit under their tile
    cfs = min(fs, fs * 0.94 * tile / max(1.0, max(tw(CALL_NAMES[c["call"]]) for c in cols)))
    bar_len = um / st.pixel_um * s if um else 0
    bh = max(2, round(0.06 * tile))
    bx1, by1 = gx0 + gw - round(0.08 * tile), gy0 + gh - round(0.08 * tile)

    def cx(j):
        return gx0 + j * (tile + gap)

    def cy(i):
        return gy0 + i * (tile + gap)

    stamp = _time.strftime("%Y-%m-%d %H%M%S")
    root = exp.results_dir / "figures" / "Images"
    root.mkdir(parents=True, exist_ok=True)
    base = _safe(f"Track {name.replace('·', '-')} montage {stamp}")
    path, k = root / f"{base}.{fmt}", 2
    while path.exists():
        path, k = root / f"{base} ({k}).{fmt}", k + 1

    def big(arr):
        a = (arr * 255).astype(np.uint8)
        return np.repeat(np.repeat(a, s, axis=0), s, axis=1) if s > 1 else a

    if fmt == "svg":
        def esc(v):
            return str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        ff = 'font-family="Helvetica, Arial, sans-serif"'
        out = [f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{W}" height="{H}" '
               f'viewBox="0 0 {W} {H}" {ff}><rect width="{W}" height="{H}" fill="#FFFFFF"/>']
        if title:
            out.append(f'<text x="{pad}" y="{pad + fs:.1f}" font-size="{fs:.1f}" font-weight="700" fill="#000">{esc(head_txt)}</text>')
        for j, c in enumerate(cols):
            xm = cx(j) + tile / 2
            if times:
                h = hours[c["frame"]] if c["frame"] < len(hours) else 0.0
                out.append(f'<text x="{xm:.1f}" y="{gy0 - 0.45 * fs:.1f}" font-size="{fs:.1f}" text-anchor="middle" fill="#000">{esc(fmt_hours(h))}</text>')
            for i, r in enumerate(rows):
                buf = io.BytesIO()
                Image.fromarray(big(c["tiles"][r])).save(buf, format="PNG")
                b64 = base64.b64encode(buf.getvalue()).decode()
                out.append(f'<image x="{cx(j)}" y="{cy(i)}" width="{tile}" height="{tile}" preserveAspectRatio="none" '
                           f'style="image-rendering:pixelated" xlink:href="data:image/png;base64,{b64}"/>')
            if calls:
                col = _hex(CALL_RGB[c["call"]])
                out.append(f'<text x="{xm:.1f}" y="{gy0 + gh + 1.05 * fs:.1f}" font-size="{cfs:.1f}" font-weight="700" text-anchor="middle" fill="{col}">{CALL_NAMES[c["call"]]}</text>')
        for i, r in enumerate(rows):
            out.append(f'<text x="{gx0 - pad / 2:.1f}" y="{cy(i) + tile / 2 + 0.35 * fs:.1f}" font-size="{fs:.1f}" text-anchor="end" '
                       f'fill="{_hex(MONT_NAME_RGB[r])}">{MONT_NAMES[r]}</text>')
        if um:
            out.append(f'<rect x="{bx1 - bar_len:.1f}" y="{by1 - bh}" width="{bar_len:.1f}" height="{bh}" fill="#FFFFFF" stroke="#000" stroke-width="{max(1, s / 2):.1f}"/>')
            out.append(f'<text x="{gx0 + gw:.1f}" y="{H - pad - 0.35 * fs:.1f}" font-size="{0.85 * fs:.1f}" text-anchor="end" fill="#000">Scale bar, {um} µm</text>')
        out.append("</svg>")
        path.write_text('<?xml version="1.0" encoding="UTF-8"?>\n' + "".join(out), encoding="utf-8")
    else:
        im = Image.new("RGB", (int(W), int(H)), (255, 255, 255))
        for j, c in enumerate(cols):
            for i, r in enumerate(rows):
                im.paste(Image.fromarray(big(c["tiles"][r])), (int(cx(j)), int(cy(i))))
        d = ImageDraw.Draw(im)
        if title:
            d.text((pad, pad), head_txt, fill=(0, 0, 0), font=_font(fs), anchor="lt", stroke_width=max(0, round(fs / 30)),
                   stroke_fill=(0, 0, 0))
        for j, c in enumerate(cols):
            xm = cx(j) + tile / 2
            if times:
                h = hours[c["frame"]] if c["frame"] < len(hours) else 0.0
                d.text((xm, gy0 - 0.45 * fs), fmt_hours(h), fill=(0, 0, 0), font=f_lab, anchor="ms")
            if calls:
                d.text((xm, gy0 + gh + 1.05 * fs), CALL_NAMES[c["call"]], fill=CALL_RGB[c["call"]], font=_font(cfs), anchor="ms",
                       stroke_width=max(0, round(cfs / 30)), stroke_fill=CALL_RGB[c["call"]])
        for i, r in enumerate(rows):
            d.text((gx0 - pad / 2, cy(i) + tile / 2), MONT_NAMES[r], fill=MONT_NAME_RGB[r], font=f_lab, anchor="rm")
        if um:
            ow = max(1, round(s / 2))
            d.rectangle([bx1 - bar_len - ow, by1 - bh - ow, bx1 + ow, by1 + ow], fill=(0, 0, 0))
            d.rectangle([bx1 - bar_len, by1 - bh, bx1, by1], fill=(255, 255, 255))
            d.text((gx0 + gw, H - pad - 0.35 * fs), f"Scale bar, {um} µm", fill=(0, 0, 0), font=_font(0.85 * fs), anchor="rs")
        if fmt == "tif":
            im.save(path, format="TIFF", compression="tiff_lzw", dpi=(300, 300))
        else:
            im.save(path, format="PNG", dpi=(300, 300))
    return {"path": str(path), "folder": str(root), "size": [int(W), int(H)], "columns": len(cols)}
