"""Linking nuclei across frames: frame-to-frame assignment, gap closing and division detection.

Distances are in pixels and gaps in frames, both derived from the experiment's physical
settings (um, hours), so 30 min and 60 min data use the same code.
"""

from __future__ import annotations

import numpy as np
from scipy.optimize import linear_sum_assignment
from scipy.spatial import cKDTree

BIG = 1e12
W_COLOUR = 5.0  # cost of a colour change between frames (normalised red/green)
ADAPTIVE_RADIUS = True


class _UF:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, a):
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


def link_frames(p0: np.ndarray, p1: np.ndarray, radius: float, f0: np.ndarray | None = None,
                f1: np.ndarray | None = None, w_int: float = 2.0) -> list[tuple[int, int]]:
    """Optimal one-to-one links between two point sets within radius (augmented LAP per cluster).

    f0/f1 are optional per-point colour features (normalised red, green). A change of colour
    raises the cost, which keeps neighbours in different phases from swapping identities.
    """
    if len(p0) == 0 or len(p1) == 0:
        return []
    tree = cKDTree(p1)
    cand = tree.query_ball_point(p0, r=radius)
    n0 = len(p0)
    uf = _UF(n0 + len(p1))
    pairs = []
    for i, js in enumerate(cand):
        for j in js:
            pairs.append((i, j))
            uf.union(i, n0 + j)
    if not pairs:
        return []
    comps = {}
    for i, j in pairs:
        comps.setdefault(uf.find(i), []).append((i, j))
    alt = radius * radius * 1.05
    links = []
    for plist in comps.values():
        rows = sorted({i for i, _ in plist})
        cols = sorted({j for _, j in plist})
        if len(rows) == 1 and len(cols) == 1:
            links.append((rows[0], cols[0]))
            continue
        ri = {r: k for k, r in enumerate(rows)}
        ci = {c: k for k, c in enumerate(cols)}
        nr, nc = len(rows), len(cols)
        size = nr + nc
        C = np.full((size, size), BIG)
        for i, j in plist:
            d2 = float(np.sum((p0[i] - p1[j]) ** 2))
            if f0 is not None:
                d2 *= 1.0 + w_int * float(np.sum((f0[i] - f1[j]) ** 2))
            C[ri[i], ci[j]] = d2
            C[nr + ci[j], nc + ri[i]] = 0.0  # auxiliary block (transpose pattern)
        C[np.arange(nr), nc + np.arange(nr)] = alt  # death
        C[nr + np.arange(nc), np.arange(nc)] = alt  # birth
        r, c = linear_sum_assignment(C)
        for a, b in zip(r, c):
            if a < nr and b < nc and C[a, b] < BIG:
                links.append((rows[a], cols[b]))
    return links


def track_movie(frames: np.ndarray, xy: np.ndarray, red: np.ndarray, green: np.ndarray,
                area: np.ndarray, radius: float, max_gap: int, daughter_radius: float,
                nucleus_px: float, min_cycle_frames: int = 4, colourless_frames: int = 0,
                present=None, last_frame: int | None = None) -> dict:
    """Return per-detection track ids, parent ids per track, per-track flags, and filled-in
    (colourless) positions.

    frames: (n,) int frame index of each detection; xy: (n, 2) registered coordinates.
    red/green: per-detection intensities (any scale; normalised inside per movie).
    colourless_frames: how long a cell may go without colour and still be followed.
    present(frame, x_reg, y_reg) -> bool: phase contrast shows a cell there (or None).
    """
    n = len(frames)
    out_tid = np.full(n, -1, dtype=np.int64)
    if n == 0:
        return {"track_id": out_tid, "parent": {}, "low_conf": set(), "virtual": []}
    order = np.argsort(frames, kind="stable")
    uniq = np.unique(frames)
    by_frame = {f: order[frames[order] == f] for f in uniq}

    # scale-free intensities for division heuristics
    def norm(v):
        v = np.nan_to_num(np.asarray(v, dtype=np.float64), nan=0.0)
        s = np.percentile(v, 95) if v.size else 1.0
        return np.clip(v / (s if s > 0 else 1.0), 0, None)

    rn, gn = norm(red), norm(green)
    feat = np.stack([np.clip(rn, 0, 1.5), np.clip(gn, 0, 1.5)], axis=1)

    # How far cells really move here: a first pass with the user's limit, then the radius is
    # tightened to what the confident links show, so a cell that goes dark is not replaced by
    # a neighbour that happens to be within the (generous) user limit.
    step = []
    pairs = list(zip(uniq[:-1], uniq[1:]))
    for f0, f1 in pairs[:: max(1, len(pairs) // 25)]:
        if f1 - f0 != 1:
            continue
        i0, i1 = by_frame[f0], by_frame[f1]
        for a, b in link_frames(xy[i0], xy[i1], radius, feat[i0], feat[i1], w_int=W_COLOUR):
            step.append(float(np.hypot(*(xy[i0[a]] - xy[i1[b]]))))
    if ADAPTIVE_RADIUS and len(step) >= 30:
        radius = float(min(radius, max(0.6 * nucleus_px, 1.5 * np.percentile(step, 95))))

    nxt = np.full(n, -1, dtype=np.int64)
    prv = np.full(n, -1, dtype=np.int64)
    for f0, f1 in pairs:
        if f1 - f0 != 1:
            continue
        i0, i1 = by_frame[f0], by_frame[f1]
        for a, b in link_frames(xy[i0], xy[i1], radius, feat[i0], feat[i1], w_int=W_COLOUR):
            nxt[i0[a]] = i1[b]
            prv[i1[b]] = i0[a]

    # tracklets
    seg_of = np.full(n, -1, dtype=np.int64)
    segs = []  # list of detection-index lists
    for d in order:
        if prv[d] == -1:
            cur, members = d, []
            while cur != -1:
                members.append(cur)
                cur = nxt[cur]
            sid = len(segs)
            for m in members:
                seg_of[m] = sid
            segs.append(members)

    # gap closing (greedy by cost)
    last_frame = int(uniq.max())
    if max_gap >= 1 and len(segs) > 1:
        starts = np.array([frames[s[0]] for s in segs])
        start_xy = np.array([xy[s[0]] for s in segs])
        tree = cKDTree(start_xy)
        cands = []
        for sid, s in enumerate(segs):
            fe = frames[s[-1]]
            if fe >= last_frame:
                continue
            maxr = radius * np.sqrt(max_gap + 1)
            for tid in tree.query_ball_point(xy[s[-1]], r=maxr):
                gap = starts[tid] - fe
                if 2 <= gap <= max_gap + 1:
                    d2 = float(np.sum((xy[s[-1]] - start_xy[tid]) ** 2))
                    if d2 <= (radius ** 2) * gap:
                        cands.append((d2 / gap, sid, tid))
        cands.sort()
        used_end, used_start = set(), set()
        join = {}
        for _, a, b in cands:
            if a in used_end or b in used_start or a == b:
                continue
            used_end.add(a)
            used_start.add(b)
            join[a] = b
        # merge chains
        merged_into = {}
        for a in sorted(join, key=lambda s: frames[segs[s][0]]):
            root = a
            while root in merged_into:
                root = merged_into[root]
            b = join[a]
            segs[root].extend(segs[b])
            merged_into[b] = root
            segs[b] = []
        segs = [s for s in segs if s]
        for sid, s in enumerate(segs):
            for m in s:
                seg_of[m] = sid

    # division detection
    parent: dict[int, int] = {}
    low_conf: set[int] = set()
    det_at = {}  # (seg, frame) -> detection
    for sid, s in enumerate(segs):
        for m in s:
            det_at[(sid, int(frames[m]))] = m
    trees = {}

    def near(f, point, r):
        idx = by_frame.get(f)
        if idx is None or len(idx) == 0:
            return []
        if f not in trees:
            trees[f] = cKDTree(xy[idx])
        return [int(idx[k]) for k in trees[f].query_ball_point(point, r)]

    first_frame = int(uniq.min())
    new_starts = [sid for sid, s in enumerate(segs) if frames[s[0]] > first_frame]
    new_starts.sort(key=lambda sid: frames[segs[sid][0]])
    for sid in new_starts:
        if not segs[sid] or sid in parent:
            continue
        ts = int(frames[segs[sid][0]])
        b0 = segs[sid][0]
        best = None
        for pd in near(ts - 1, xy[b0], daughter_radius):
            pid = int(seg_of[pd])
            if pid == sid or not segs[pid]:
                continue
            # PIP-FUCCI: the parent is red (G2/M) the frame before, and mCherry-Geminin drops
            # at anaphase, so the daughters' red is clearly lower than the parent's
            if rn[pd] < 0.15:
                continue
            if rn[b0] > 0.6 * rn[pd] + 0.02:
                continue
            # a cell born by division cannot divide again within a minimum cycle time,
            # and must have gone through S (red up, green down) first
            if pid in parent:
                if ts - frames[segs[pid][0]] < min_cycle_frames:
                    continue
                if not any(rn[m] > 0.3 and gn[m] < 0.3 for m in segs[pid] if frames[m] < ts):
                    continue
            d = float(np.hypot(*(xy[pd] - xy[b0])))
            sib = det_at.get((pid, ts))
            if sib is not None:
                if rn[sib] > 0.6 * rn[pd] + 0.02:
                    continue
                # daughters are near-identical twins at birth
                if not (0.55 < area[sib] / max(area[b0], 1e-6) < 1.8):
                    continue
                if not (0.4 < (gn[sib] + 0.05) / (gn[b0] + 0.05) < 2.5):
                    continue
                sep = float(np.hypot(*(xy[sib] - xy[b0])))
                if sep > 3.5 * nucleus_px or sep < 0.6 * nucleus_px:
                    continue
                # daughters sit on either side of where the parent was
                mid = 0.5 * (xy[sib] + xy[b0])
                score = float(np.hypot(*(mid - xy[pd]))) + 0.25 * d
            else:
                score = d
            # PIP-FUCCI: parent red (G2/M), daughters green with red dropping
            if rn[pd] >= 0.3:
                score -= 0.3 * nucleus_px
            if gn[b0] > rn[b0]:
                score -= 0.1 * nucleus_px
            if best is None or score < best[0]:
                best = (score, pid, pd)
        if best is None:
            continue
        _, pid, pd = best
        sib = det_at.get((pid, ts))
        if sib is not None:
            # split the parent: frames >= ts become daughter A
            s = segs[pid]
            keep = [m for m in s if frames[m] < ts]
            tail = [m for m in s if frames[m] >= ts]
            segs[pid] = keep
            new_id = len(segs)
            segs.append(tail)
            for m in tail:
                det_at.pop((pid, int(frames[m])), None)
                det_at[(new_id, int(frames[m]))] = m
                seg_of[m] = new_id
            daughters = [new_id, sid]
        else:
            # parent ended at ts-1: look for a second new start next to this one
            daughters = [sid]
            for od in near(ts, xy[b0], 2.5 * daughter_radius):
                oid = int(seg_of[od])
                if oid == sid or oid in parent or not segs[oid] or segs[oid][0] != od:
                    continue
                daughters.append(oid)
                break
            if len(daughters) < 2:
                # a single new start beside an ended track: likely one daughter lost
                low_conf.add(sid)
        for dsid in daughters:
            parent[dsid] = pid
        # biological plausibility: parent red (G2/M) and daughters lower red
        pr = rn[pd]
        drs = [rn[segs[x][0]] for x in daughters if segs[x]]
        if pr < 0.25 or (drs and max(drs) > 0.8 * pr + 0.05):
            low_conf.update(daughters)
        areas = [area[segs[x][0]] for x in daughters if segs[x]]
        if areas and max(areas) > 1.3 * area[pd]:
            low_conf.update(daughters)

    virtual: dict[int, list] = {}
    if colourless_frames > max_gap + 1 and present is not None:
        lf = int(uniq.max()) if last_frame is None else int(last_frame)
        def occupied(t, x, y):  # a coloured nucleus is already detected there
            return len(near(t, np.array([x, y]), 0.6 * nucleus_px)) > 0

        _bridge_colourless(segs, frames, xy, rn, gn, parent, low_conf, virtual, radius, max_gap,
                           colourless_frames, nucleus_px, present, int(uniq.min()), lf, occupied)

    # final ids
    for sid, s in enumerate(segs):
        for m in s:
            out_tid[m] = sid
    # drop references to emptied segments
    parent = {k: v for k, v in parent.items() if segs[k] and segs[v]}
    low_conf = {k for k in low_conf if segs[k]}
    vlist = [(sid, f, x, y) for sid, pts in virtual.items() if segs[sid] for (f, x, y) in pts]
    return {"track_id": out_tid, "parent": parent, "low_conf": low_conf, "virtual": vlist, "radius": radius}


def _path_present(present, pa, pb, fa, fb, need=0.6):
    """Share of the frames strictly between fa and fb where phase contrast shows a cell on the
    straight path from pa to pb."""
    gap = fb - fa
    if gap <= 1:
        return 1.0, []
    pts, ok = [], 0
    for t in range(fa + 1, fb):
        a = (t - fa) / gap
        x, y = pa[0] + (pb[0] - pa[0]) * a, pa[1] + (pb[1] - pa[1]) * a
        pts.append((t, float(x), float(y)))
        ok += bool(present(t, x, y))
    return ok / (gap - 1), pts


def _bridge_colourless(segs, frames, xy, rn, gn, parent, low_conf, virtual, radius, max_gap, cl_frames,
                       nucleus_px, present, first_frame, last_frame, occupied=lambda t, x, y: False):
    """Follow cells through stretches where both reporters are off (PIP-FUCCI 'colourless').

    1. A track that stops and a track that starts nearby later, with a cell visible in phase
       contrast in between, are the same cell (or, if the first ended red, mother and daughters).
    2. A track that stops while phase contrast still shows a cell there carries on as colourless,
       and a track that starts mid-movie is extended backwards the same way.
    The filled-in positions are returned in `virtual`; intensities are measured later.
    """
    divided = set(parent.values())
    ends = [a for a, s in enumerate(segs) if s and frames[s[-1]] < last_frame and a not in divided]
    starts = [b for b, s in enumerate(segs) if s and frames[s[0]] > first_frame and b not in parent]
    cap = 4.0 * nucleus_px
    cands = []
    if ends and starts:
        sp = np.array([xy[segs[b][0]] for b in starts])
        tree = cKDTree(sp)
        for a in ends:
            la = segs[a][-1]
            fe = int(frames[la])
            for k in tree.query_ball_point(xy[la], r=cap):
                b = starts[k]
                if b == a:
                    continue
                fs = int(frames[segs[b][0]])
                gap = fs - fe
                if not (max_gap + 2 <= gap <= cl_frames + 1):
                    continue
                d = float(np.hypot(*(xy[segs[b][0]] - xy[la])))
                if d > min(radius * np.sqrt(gap), cap):
                    continue
                cands.append((d / np.sqrt(gap), a, b))
    cands.sort()
    used_b = set()
    taken = {}  # a -> list of b
    for _, a, b in cands:
        if b in used_b or not segs[a] or not segs[b]:
            continue
        la, fb = segs[a][-1], segs[b][0]
        fe, fs = int(frames[la]), int(frames[fb])
        frac, pts = _path_present(present, xy[la], xy[fb], fe, fs)
        if frac < 0.6:
            continue
        # S, G2 and M all carry red; a cell that went dark with red and comes back green with
        # (much) less red has divided in between: what comes back is a daughter, not the same cell
        mother = rn[la] >= 0.15 and rn[fb] < 0.6 * rn[la] and gn[fb] > rn[fb]
        if mother:
            if len(taken.get(a, [])) >= 2:
                continue
            taken.setdefault(a, []).append(b)
            parent[b] = a
            low_conf.add(b)
            virtual.setdefault(b, []).extend(pts)
            used_b.add(b)
        else:
            if a in taken:
                continue
            taken[a] = [b]
            used_b.add(b)
            virtual.setdefault(a, []).extend(pts)
            # b continues a: move b's detections (and its daughters) to a
            segs[a].extend(segs[b])
            virtual.setdefault(a, []).extend(virtual.pop(b, []))
            segs[b] = []
            for k, v in list(parent.items()):
                if v == b:
                    parent[k] = a
            if b in low_conf:
                low_conf.discard(b)
    # 2. tails and heads: the cell is still there, just without colour
    divided = set(parent.values())
    for a, s in enumerate(segs):
        if not s or a in divided:
            continue
        la = s[-1]
        fe = int(frames[la])
        if fe < last_frame:
            ext, miss = [], 0
            vx, vy = float(xy[la][0]), float(xy[la][1])
            for t in range(fe + 1, min(last_frame, fe + cl_frames) + 1):
                if occupied(t, vx, vy):
                    break
                if present(t, vx, vy):
                    ext.append((t, vx, vy))
                    miss = 0
                else:
                    miss += 1
                    if miss >= 2:
                        break
            while ext and not present(ext[-1][0], vx, vy):
                ext.pop()
            if ext:
                virtual.setdefault(a, []).extend(ext)
    for b, s in enumerate(segs):
        if not s or b in parent:
            continue
        fb = s[0]
        fs = int(frames[fb])
        if fs > first_frame and b not in used_b:
            ext, miss = [], 0
            vx, vy = float(xy[fb][0]), float(xy[fb][1])
            for t in range(fs - 1, max(first_frame, fs - cl_frames) - 1, -1):
                if occupied(t, vx, vy):
                    break
                if present(t, vx, vy):
                    ext.append((t, vx, vy))
                    miss = 0
                else:
                    miss += 1
                    if miss >= 2:
                        break
            while ext and not present(ext[-1][0], vx, vy):
                ext.pop()
            if ext:
                virtual.setdefault(b, []).extend(ext)
