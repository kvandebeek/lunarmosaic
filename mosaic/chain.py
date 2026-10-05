"""Chain documents and their execution (plan §4.4–4.5): validation, the statistics pass, tiled rendering, merge.

A chain document holds three ordered block lists — luminance, colour, final — a merge strength and the preview
rectangles. `prepare()` computes every global statistic once, on a 1/4 proxy of the whole image and on a
full-resolution probe crop (D1); `render()` then processes any region with a halo and gives the same pixels
whether the image is done whole, in strips or in tiles.
"""
import copy
import json
import re
import secrets

import cv2
import numpy as np

import blocks as B
import filters as F
import measurements as ms
import mosaic_common as mc

FORMAT, VERSION = 'lunarmosaic-chain', 1
STATS_Q = 4           # as lunar_finish: changing it moved the highlights/contrast tone 120–324 ADU rms (plan D1)
PATHS = B.PATHS


# ---------------------------------------------------------------- the document

def empty():
    return {'format': FORMAT, 'version': VERSION, 'protect_space': True,
            'chains': {'luminance': [], 'colour': [], 'merge': {'strength': 1.0}, 'final': []}, 'previews': []}


def entry(type, params=None, limb_fade_px=0.0, enabled=True):
    """A new block entry with a fresh id; parameters are filled with defaults and checked."""
    return {'id': secrets.token_hex(4), 'type': type, 'enabled': bool(enabled), 'limb_fade_px': float(limb_fade_px),
            'params': B.BLOCKS[type].normalise(params or {})}


def validate(doc):
    """[(block id or None, reason)] — empty when the document is valid. Reasons are sentences for the interface,
    which shows a refused block greyed with its reason rather than hiding it."""
    if not isinstance(doc, dict) or doc.get('format') != FORMAT:
        return [(None, 'This is not a LunarMosaic chain document.')]
    if doc.get('version') != VERSION:
        return [(None, f'Chain documents of version {doc.get("version")!r} are not supported.')]
    chains = doc.get('chains')
    if not isinstance(chains, dict) or any(not isinstance(chains.get(k), list) for k in PATHS):
        return [(None, 'The chain document needs luminance, colour and final block lists.')]
    problems, seen = [], set()
    if not isinstance(doc.get('protect_space', True), bool):
        problems.append((None, 'protect_space must be true or false.'))
    strength = (chains.get('merge') or {}).get('strength', 1.0)
    if isinstance(strength, bool) or not isinstance(strength, (int, float)) or not 0 <= strength <= 1:
        problems.append((None, 'The merge strength must be between 0 and 1.'))
    for path in PATHS:
        for e in chains[path]:
            bid = e.get('id') if isinstance(e, dict) else None
            try:
                if not isinstance(e, dict) or not isinstance(bid, str) or not re.fullmatch(r'[0-9a-f]{8}', bid):
                    raise ValueError('Every block needs an 8-character hexadecimal id.')
                if bid in seen:
                    raise ValueError('Two blocks share this id.')
                seen.add(bid)
                blk = B.BLOCKS.get(e.get('type'))
                if blk is None:
                    raise ValueError(f'Unknown block type {e.get("type")!r}.')
                if path not in blk.paths:
                    raise ValueError(f'{blk.type.replace("_", " ").capitalize()} does not work on the {path} path.')
                if not isinstance(e.get('enabled', True), bool):
                    raise ValueError('enabled must be true or false.')
                fade = e.get('limb_fade_px', 0)
                if isinstance(fade, bool) or not isinstance(fade, (int, float)) or not fade >= 0:
                    raise ValueError('The limb fade must be zero or a positive number of pixels.')
                blk.normalise(e.get('params', {}))
            except ValueError as err:
                problems.append((bid, str(err)))
    return problems


def normalised(doc):
    """A validated copy with every block's parameters filled in; raises ValueError listing the problems."""
    problems = validate(doc)
    if problems:
        raise ValueError(' '.join(r if i is None else f'Block {i}: {r}' for i, r in problems))
    doc = copy.deepcopy(doc)
    for path in PATHS:
        for e in doc['chains'][path]:
            e['params'] = B.BLOCKS[e['type']].normalise(e.get('params', {}))
            e.setdefault('enabled', True)
            e['limb_fade_px'] = float(e.get('limb_fade_px', 0))
    doc['chains'].setdefault('merge', {'strength': 1.0})
    doc.setdefault('protect_space', True)
    doc.setdefault('previews', [])
    return doc


def load(path):
    with open(path, encoding='utf-8') as fh:
        return normalised(json.load(fh))


def dump(doc, path):
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(doc, fh, indent=2)
        fh.write('\n')


def enabled(doc, path):
    return [e for e in doc['chains'][path] if e.get('enabled', True)]


def margin(doc):
    """The halo a region needs so that its core matches a whole-image render (+3 px of safety)."""
    return sum(B.BLOCKS[e['type']].margin(e['params']) for p in PATHS for e in enabled(doc, p)) + 3


def default_chain(args, mono):
    """lunar_finish's flags as a chain (plan Appendix E): every block in the final chain, in the old order."""
    doc = empty()
    fin = doc['chains']['final']
    if args.wavelets:
        fin.append(entry('wavelets', {'biases': list(args.wavelets), 'kernel': args.wavelet_kernel,
                                      'denoise': list(args.wavelet_denoise)}))
    if args.highlights > 0:
        fin.append(entry('highlights', {'strength': args.highlights, 'knee_percentile': args.knee_percentile}))
    # chroma smoothing ran even with no saturation pass; a mono image has no colour to touch
    if not mono and (args.saturation_passes > 0 or args.chroma_denoise > 0):
        fin.append(entry('saturation', {'factor': args.saturation, 'passes': args.saturation_passes, 'lunar_hue': True,
                                        'chroma_blur_px': args.chroma_denoise},
                         limb_fade_px=max(args.limb_taper, 1)))       # the old taper divided by max(taper, 1)
    if args.contrast > 0:
        fin.append(entry('contrast', {'strength': args.contrast}))
    if args.usm:
        fin.append(entry('unsharp_mask', {'amount': args.usm[0] * 100, 'radius': args.usm[1],
                                          'threshold': args.usm[2], 'mode': args.usm_mode}))
    return doc


# ---------------------------------------------------------------- sources, statistics, regions

class ArraySource:
    """An in-memory 8- or 16-bit RGB image (H × W × 3) as a chain source. The proxy is made exactly as lunar_finish
    made it — whole q × q blocks, INTER_AREA on the integer data — so its statistics carry over unchanged."""

    def __init__(self, img):
        if img.ndim != 3 or img.shape[2] != 3 or img.dtype not in (np.uint8, np.uint16):
            raise ValueError('Chain sources are 8- or 16-bit RGB images.')
        self.img, self.h, self.w = img, img.shape[0], img.shape[1]
        self.scale = 65535.0 if img.dtype == np.uint16 else 255.0

    def region(self, y0, x0, h, w):
        return self.img[y0:y0 + h, x0:x0 + w].astype(np.float32) / self.scale

    def proxy(self, q):
        src = mc.proxy_crop(self.img, q)
        if src.dtype == np.uint8:                 # 8-bit: average in float, not in 1/255 steps
            src = src.astype(np.float32)
        return cv2.resize(src, (self.w // q, self.h // q), interpolation=cv2.INTER_AREA).astype(np.float32) / self.scale


class Stats:
    """Everything chain.prepare measured: the proxy-grid maps, per-block statistics and readouts."""

    def __init__(self, q, h, w, disk, filled, limb, space):
        self.q, self.h, self.w = q, h, w
        # proxy grid: eroded lit disk, hole-filled disk, limb distance (canvas px), space region
        self.disk, self.filled, self.limb, self.space = disk, filled, limb, space
        self.blocks, self.readouts = {}, {}

    def at(self, m, y0, x0, h, w):
        """A proxy-grid map sampled for a canvas region (pixel-centre mapping of whole q × q blocks)."""
        q = self.q
        X, Y = np.meshgrid(np.arange(x0, x0 + w, dtype=np.float32), np.arange(y0, y0 + h, dtype=np.float32))
        mx, my = (X - (q - 1) / 2) / q, (Y - (q - 1) / 2) / q
        return cv2.remap(m, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


class Ctx:
    """Where a block is working: a canvas region at scale 1, or the whole proxy grid (scale 1/q)."""

    def __init__(self, stats, y0=0, x0=0, h=0, w=0, proxy=False):
        self.stats, self.y0, self.x0, self.h, self.w, self.proxy = stats, y0, x0, h, w, proxy
        self.q = stats.q
        self.scale = 1.0 / stats.q if proxy else 1.0
        self._cache = {}

    def at(self, m):
        if self.proxy:
            return m
        key = id(m)
        if key not in self._cache:
            self._cache[key] = self.stats.at(m, self.y0, self.x0, self.h, self.w)
        return self._cache[key]


def shadow_gate(Lstar):
    """1 at input L* ≤ 1 fading to 0 at L* 2: how far a near-black pixel's colour is held at its input's."""
    return 1 - np.clip((Lstar - 1.0) / 1.0, 0, 1)


class Protection:
    """D7 for one chain input.

    Space — the frame-connected dark region s — is restored outright after every block (at the end of a Lab run),
    with a soft gate t on the input's L* (1 below 0.5, 0 above 1.5), so later filters see black sky. And at the
    chain's end, near-black pixels anywhere are held at their input's colour: a*b* go back by shadow_gate (1 below
    L* 1, 0 from L* 2), while their lightness is processed like any other pixel's, so a shadow lift stays smooth.
    The hold is applied once, at the end: applied after every block it changed what saturation's chroma blur
    spread into the crater rims (60 ADU there, measured), unlike the old rule, which froze outputs only. Measured without the colour hold: the default chain's 3 px chroma blur carried a
    crater rim's colour into its shadow and saturation ×4 took it out of gamut — L* 0.76, chroma 0.10 became
    RGB (0, 1053, 7717), chroma 15.3; the old rule froze those pixels whole, which posterised shadow lifts."""

    def __init__(self, inp, ctx, on=True):
        self.inp, self.idx, self.p, self.cidx = inp, None, None, None
        if not on:
            return
        s = ctx.at(ctx.stats.space)
        # only dark pixels can be protected (L* 2 is a stored value near 0.03): measure L* there only
        cand = (s > 0) | (inp.max(axis=-1) < 0.05)
        if not cand.any():
            return
        L = np.full(s.shape, 100.0, np.float32)
        L[cand] = F.lstar(inp[cand])
        p = s * (1 - np.clip((L - 0.5) / 1.0, 0, 1))
        idx = p > 0
        if idx.any():
            self.idx, self.p = idx, p.astype(np.float32)
            self._w, self._sel = self.p[idx][:, None], inp[idx]
        c = shadow_gate(L)
        cidx = (c > 0) & (p < 1)
        if cidx.any():
            self.cidx, self._c = cidx, c[cidx][:, None]
            self._ab = F.rgb_to_lab(inp[cidx])[:, 1:]

    def apply(self, x):
        """After every block: space goes back to the input."""
        if self.idx is not None:
            x[self.idx] = self._w * self._sel + (1 - self._w) * x[self.idx]
        return x

    def finish(self, x):
        """At the chain's end: near-black pixels keep their input's colour, whatever their lightness; then space."""
        if self.cidx is not None:
            lab = F.rgb_to_lab(x[self.cidx])
            lab[:, 1:] = self._c * self._ab + (1 - self._c) * lab[:, 1:]
            x[self.cidx] = np.clip(F.lab_to_rgb(lab), 0, 1)
        return self.apply(x)


def merge(lum, col, cov_l=None, cov_c=None, strength=1.0):
    """D11: CIE Lab lightness from the luminance result, a*b* from the colour result × strength × coverage. With no
    colour the luminance result comes out grey."""
    ll = F.rgb_to_lab(lum)
    out = np.empty_like(ll)
    out[..., 0] = ll[..., 0]
    if col is None:
        out[..., 1:] = 0
    else:
        w = strength * np.minimum(1.0 if cov_l is None else cov_l, 1.0 if cov_c is None else cov_c)
        out[..., 1:] = F.rgb_to_lab(col)[..., 1:] * (np.asarray(w, np.float32)[..., None] if np.ndim(w) else w)
    return np.clip(F.lab_to_rgb(out), 0, 1)


# ---------------------------------------------------------------- running blocks

def _fade(e, blk, before, after, ctx):
    """D6 for every block but saturation (which fades its own factor): out = in + w·(block(in) − in)."""
    fade = e.get('limb_fade_px', 0)
    if blk.fades_itself or fade <= 0:
        return after
    w = np.clip(ctx.at(ctx.stats.limb) / fade, 0, 1)[..., None]
    return before + w * (after - before)


def run_chain(entries, x, ctx, *, protect=None, snapshot=None, hook=None, skip_detail=False):
    """Run enabled block entries on a float32 RGB region. Adjacent Lab blocks share one conversion: inside such a
    run L* is clipped to 0–100 after every block (as lunar_finish clipped L), RGB at the run's end, and space is
    protected there and after every RGB block. hook(entry, block, x, domain) is called before a block runs (the
    statistics pass); snapshot(entry, array, domain, step) after it, with step = the saturation pass."""
    i, n = 0, len(entries)
    blk_of = lambda e: B.BLOCKS[e['type']]
    while i < n:
        e = entries[i]
        blk = blk_of(e)
        if skip_detail and blk.detail:
            i += 1
            continue
        st = ctx.stats.blocks.setdefault(e['id'], {})
        if blk.domain(e['params']) == 'lab':
            lab = F.rgb_to_lab(x)
            while i < n and blk_of(entries[i]).domain(entries[i]['params']) == 'lab':
                e = entries[i]
                blk = blk_of(e)
                i += 1
                if skip_detail and blk.detail:
                    continue
                st = ctx.stats.blocks.setdefault(e['id'], {})
                if hook:
                    hook(e, blk, lab, 'lab')
                    st = ctx.stats.blocks[e['id']]
                kw, passes = {}, []
                if blk.fades_itself:
                    kw['limb_fade_px'] = e.get('limb_fade_px', 0)
                    if snapshot:
                        kw['on_pass'] = lambda k, a, e=e: (passes.append(k), snapshot(e, a, 'lab', k))
                new = _fade(e, blk, lab, blk.apply(lab, e['params'], st, ctx, **kw), ctx)
                new[..., 0] = np.clip(new[..., 0], 0, 100)
                lab = new
                if snapshot and not passes:
                    snapshot(e, lab, 'lab', None)
            x = np.clip(F.lab_to_rgb(lab), 0, 1)
            if protect:
                protect.apply(x)
        else:
            i += 1
            if hook:
                hook(e, blk, x, 'rgb')
                st = ctx.stats.blocks[e['id']]
            x = np.clip(_fade(e, blk, x, blk.apply(x, e['params'], st, ctx), ctx), 0, 1).astype(np.float32)
            if protect:
                protect.apply(x)
            if snapshot:
                snapshot(e, x, 'rgb', None)
    if protect and entries:
        protect.finish(x)
    return x


def prepare(source, doc, q=STATS_Q):
    """D1: every global statistic, once — on the proxy with the detail blocks skipped, then on the probe (a
    full-resolution crop at the disk's centre of mass) for the detail blocks' own statistics and the readouts."""
    H, W = source.h, source.w
    if min(H, W) < 4 * q:
        raise ValueError(f'The image is too small ({W} × {H} px).')
    proxy = source.proxy(q)
    disk, filled, limb, space = ms.disk_and_space(proxy, q)
    stats = Stats(q, H, W, disk, filled, limb, space)
    lum_e, col_e, fin_e = (enabled(doc, p) for p in PATHS)
    strength = doc['chains'].get('merge', {}).get('strength', 1.0)
    on = doc.get('protect_space', True)

    def passes(x, ctx, hook, skip_detail, snapshot=None):
        if lum_e or col_e:
            lum = run_chain(lum_e, x, ctx, protect=Protection(x, ctx, on), hook=hook, skip_detail=skip_detail, snapshot=snapshot)
            col = run_chain(col_e, x, ctx, protect=Protection(x, ctx, on), hook=hook, skip_detail=skip_detail, snapshot=snapshot)
            x = merge(lum, col, strength=strength)
        return run_chain(fin_e, x, ctx, protect=Protection(x, ctx, on), hook=hook, skip_detail=skip_detail, snapshot=snapshot)

    before = {}                                       # detail blocks' probe input, for their readouts

    def collect(kind, ctx):
        def hook(e, blk, x, dom):
            stats.blocks.setdefault(e['id'], {}).update(blk.prepare(kind, x, e['params'], ctx))
            if kind == 'probe' and blk.detail:
                before[e['id']] = np.clip(F.lab_to_rgb(x), 0, 1) if dom == 'lab' else x.copy()
        return hook

    ctxp = Ctx(stats, proxy=True)
    passes(proxy, ctxp, collect('proxy', ctxp), True)
    if any(B.BLOCKS[e['type']].detail for e in lum_e + col_e + fin_e):
        ys, xs = np.nonzero(disk)
        cy, cx, R = int(ys.mean() * q), int(xs.mean() * q), min(1024, H // 2, W // 2)
        y0, x0 = int(np.clip(cy - R, 0, H - 2 * R)), int(np.clip(cx - R, 0, W - 2 * R))
        ctx = Ctx(stats, y0, x0, 2 * R, 2 * R)

        def readout(e, arr, dom, step):
            if e['id'] in before and step is None:
                after = np.clip(F.lab_to_rgb(arr), 0, 1) if dom == 'lab' else arr
                stats.readouts.setdefault(e['id'], {})['fine_detail'] = ms.fine_detail(before.pop(e['id']), after)
        passes(source.region(y0, x0, 2 * R, 2 * R), ctx, collect('probe', ctx), False, snapshot=readout)
    for path in PATHS:
        for i, e in enumerate(enabled(doc, path)):
            if e['type'] == 'deconvolution' and i:
                stats.readouts.setdefault(e['id'], {})['warning'] = (
                    'Deconvolution works best first in its chain, on data nothing has sharpened or stretched yet.')
    return stats


def render(source, stats, doc, y0, x0, h, w, snapshot=None):
    """The finished pixels of a canvas region, computed with a halo. snapshot(label, entry, array, domain,
    protection, step) receives the region's core: 'input' first, then every final-chain block's output."""
    m = margin(doc)
    ya, yb = max(0, y0 - m), min(source.h, y0 + h + m)
    xa, xb = max(0, x0 - m), min(source.w, x0 + w + m)
    x = source.region(ya, xa, yb - ya, xb - xa)
    ctx = Ctx(stats, ya, xa, yb - ya, xb - xa)
    on = doc.get('protect_space', True)
    lum_e, col_e, fin_e = (enabled(doc, p) for p in PATHS)
    if lum_e or col_e:
        lum = run_chain(lum_e, x, ctx, protect=Protection(x, ctx, on))
        col = run_chain(col_e, x, ctx, protect=Protection(x, ctx, on))
        x = merge(lum, col, strength=doc['chains'].get('merge', {}).get('strength', 1.0))
    prot = Protection(x, ctx, on)
    core = (slice(y0 - ya, y0 - ya + h), slice(x0 - xa, x0 - xa + w))
    snap = None
    if snapshot:
        pc = None if prot.p is None else prot.p[core]
        snapshot('input', None, x[core], 'rgb', (pc, x[core]), None)
        snap = lambda e, arr, dom, step: snapshot(e['id'], e, arr[core], dom, (pc, x[core]), step)
    return run_chain(fin_e, x, ctx, protect=prot, snapshot=snap)[core]
