"""Chain blocks (plan Appendix A): parameter schemas, statistics, readouts and the pixel operations.

Phase 2 has the five operations lunar_finish already had — wavelets, unsharp mask, highlights, contrast and
saturation — reproducing it (golden fixtures). A block works on float32 RGB in [0, 1], or on CIE Lab (L* 0–100)
when its domain is "lab"; chain.run_chain gives adjacent Lab blocks one conversion (plan §4.3, D17).

Statistics come from chain.prepare, never from inside apply() (D1): a block's `prepare(kind, x, params, ctx)`
sees the proxy ("proxy") or the full-resolution probe ("probe") as it arrives at the block, in its domain.
"""
import math

import cv2
import numpy as np

import filters as F

PATHS = ('luminance', 'colour', 'final')


class Param:
    """One parameter: kind is float, int, bool, choice or floats (a list of floats, `length` = (min, max))."""

    def __init__(self, kind, default, lo=None, hi=None, lo_open=False, hi_open=False, choices=(), length=None):
        self.kind, self.default, self.lo, self.hi = kind, default, lo, hi
        self.lo_open, self.hi_open, self.choices, self.length = lo_open, hi_open, tuple(choices), length

    def check(self, name, v):
        """The value, typed, or raises ValueError with a sentence naming the parameter."""
        if self.kind == 'bool':
            if not isinstance(v, bool):
                raise ValueError(f'{name} must be true or false.')
            return v
        if self.kind == 'choice':
            if v not in self.choices:
                raise ValueError(f'{name} must be one of: {", ".join(self.choices)}.')
            return v
        if self.kind == 'floats':
            if not isinstance(v, (list, tuple)) or not self.length[0] <= len(v) <= self.length[1]:
                raise ValueError(f'{name} needs {self.length[0]} to {self.length[1]} numbers.')
            return [self._number(name, x, float) for x in v]
        return self._number(name, v, int if self.kind == 'int' else float)

    def _number(self, name, v, typ):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ValueError(f'{name} must be a number.')
        if typ is int and v != int(v):
            raise ValueError(f'{name} must be a whole number.')
        v = typ(v)
        below = self.lo is not None and (v <= self.lo if self.lo_open else v < self.lo)
        above = self.hi is not None and (v >= self.hi if self.hi_open else v > self.hi)
        if below or above:
            lo = '' if self.lo is None else f'{"above" if self.lo_open else "at least"} {self.lo:g}'
            hi = '' if self.hi is None else f'{"below" if self.hi_open else "at most"} {self.hi:g}'
            raise ValueError(f'{name} must be {" and ".join(s for s in (lo, hi) if s)}.')
        return v


class Block:
    def __init__(self, type, paths, params, apply, margin, *, domain='rgb', detail=False, prepare=None,
                 fades_itself=False):
        self.type, self.paths, self.params, self._apply, self._margin = type, paths, params, apply, margin
        self._domain, self.detail, self._prepare, self.fades_itself = domain, detail, prepare, fades_itself

    def domain(self, p):
        return self._domain(p) if callable(self._domain) else self._domain

    def margin(self, p):
        return int(self._margin(p))

    def prepare(self, kind, x, p, ctx):
        return self._prepare(kind, x, p, ctx) if self._prepare else {}

    def apply(self, x, p, st, ctx, **kw):
        return self._apply(x, p, st, ctx, **kw)

    def normalise(self, params):
        """Fill defaults and check every value; raises ValueError naming the first bad parameter."""
        if not isinstance(params, dict):
            raise ValueError('Block parameters must be an object.')
        unknown = set(params) - set(self.params)
        if unknown:
            raise ValueError(f'Unknown parameter: {", ".join(sorted(unknown))}.')
        out = {k: spec.check(k, params.get(k, spec.default)) for k, spec in self.params.items()}
        return out


BLOCKS = {}


def register(block):
    BLOCKS[block.type] = block
    return block


def fine_detail(before, after):
    """lunar_finish's fine-detail amplitude: rms ratio of G(0.8) − G(2.0) on the channel mean."""
    e = lambda im: float(np.mean((cv2.GaussianBlur(im.mean(2), (0, 0), 0.8) - cv2.GaussianBlur(im.mean(2), (0, 0), 2.0)) ** 2))
    return (e(after) / max(e(before), 1e-12)) ** 0.5


# ---------------------------------------------------------------- wavelets (detail, RGB)

def _wavelets_prepare(kind, x, p, ctx):
    if kind != 'probe':
        return {}
    # layer noise from the full-resolution probe, on stored values, as the layers are sharpened
    return {'noise': F.layer_noise(x.mean(axis=2), len(p['biases']), p['kernel'])}


def _wavelets_apply(x, p, st, ctx):
    n = len(p['biases'])
    noise = st.get('noise') or [0.0] * n          # zip() would silently drop every layer if this were empty
    denoise = (list(p['denoise']) + [0.0] * n)[:n]
    # as PixInsight on an RGB/K target: each channel on its own stored values
    return np.dstack([F.wavelet_sharpen(x[..., c], p['biases'], noise, denoise, p['kernel']) for c in range(3)])


register(Block('wavelets', PATHS, {
    'biases': Param('floats', [0.0, 0.0, 0.0, 0.0], -1, 15, length=(1, 8)),
    'kernel': Param('choice', 'linear', choices=('linear', 'b3')),
    'denoise': Param('floats', [0.0, 0.0, 0.0, 0.0], 0, None, length=(0, 8))},
    _wavelets_apply, lambda p: F.wavelet_margin(len(p['biases'])), detail=True, prepare=_wavelets_prepare))


# ---------------------------------------------------------------- unsharp mask (detail; RGB, or L* in luminosity mode)

def _usm_apply(x, p, st, ctx):
    a, r, t = p['amount'] / 100, p['radius'], p['threshold']
    if p['mode'] == 'luminosity':
        out = x.copy()
        out[..., 0] = F.unsharp(x[..., 0] / 100, a, r, t) * 100
        return out
    return F.unsharp(x, a, r, t)


register(Block('unsharp_mask', PATHS, {
    'amount': Param('float', 0.0, 0, 500),
    'radius': Param('float', 1.0, 0.1, 250),
    'threshold': Param('float', 0.0, 0, 255),
    'mode': Param('choice', 'rgb', choices=('rgb', 'luminosity'))},
    _usm_apply, lambda p: math.ceil(4 * F.usm_sigma(p['radius'])) + 1,
    domain=lambda p: 'lab' if p['mode'] == 'luminosity' else 'rgb', detail=True))


# ---------------------------------------------------------------- highlights (Lab)

def _highlights_prepare(kind, lab, p, ctx):
    if kind != 'proxy':
        return {}
    L = lab[..., 0] / 100
    A, B = F.guided_self(L, r=max(4, 64 // ctx.q), eps=0.03 ** 2)
    base = A * L + B
    disk = ctx.stats.disk
    knee = float(np.percentile(base[disk], p['knee_percentile']))
    return {'A': A.astype(np.float32), 'B': B.astype(np.float32), 'knee': knee,
            'top': float(np.percentile(base[disk], 99.99)), 'affected': float((base[disk] > knee).mean() * 100)}


def _highlights_apply(lab, p, st, ctx):
    L = lab[..., 0] / 100
    A, B = ctx.at(st['A']), ctx.at(st['B'])
    base = A * L + B
    out = lab.copy()
    # a smooth base and fine detail: the shoulder lowers only the brightest base, the detail goes back unchanged
    out[..., 0] = (F.shoulder(base, st['knee'], st['top'], p['strength']) + (L - base)) * 100
    return out


register(Block('highlights', ('luminance', 'final'), {
    'strength': Param('float', 0.30, 0, 0.5, hi_open=True),
    'knee_percentile': Param('float', 90.0, 0, 99.9, lo_open=True, hi_open=True)},
    _highlights_apply, lambda p: 0, domain='lab', prepare=_highlights_prepare))


# ---------------------------------------------------------------- contrast (Lab)

def _contrast_prepare(kind, lab, p, ctx):
    return {'m': float(np.median(lab[..., 0][ctx.stats.disk] / 100))} if kind == 'proxy' else {}


def _contrast_apply(lab, p, st, ctx):
    out = lab.copy()
    out[..., 0] = F.s_curve(lab[..., 0] / 100, st['m'], p['strength']) * 100
    return out


register(Block('contrast', ('luminance', 'final'), {
    'strength': Param('float', 0.12, 0, 0.5, hi_open=True)},
    _contrast_apply, lambda p: 0, domain='lab', prepare=_contrast_prepare))


# ---------------------------------------------------------------- saturation (Lab; fades its own factor)

def _saturation_apply(lab, p, st, ctx, limb_fade_px=0.0, on_pass=None):
    a, b = lab[..., 1], lab[..., 2]
    if p['chroma_blur_px'] > 0:                      # colour-only smoothing; brightness keeps all detail
        sigma = p['chroma_blur_px'] * ctx.scale
        a, b = cv2.GaussianBlur(a, (0, 0), sigma), cv2.GaussianBlur(b, (0, 0), sigma)
    hue = (np.degrees(np.arctan2(b, a)) + 360) % 360
    w = F.lunar_weight(hue) if p['lunar_hue'] else 1.0
    # no extra saturation at the limb, where residual colour fringing lives: the factor itself fades to 1, as
    # --limb-taper always did (a pass-by-pass fade, not a blend of the finished output)
    t = np.clip(ctx.at(ctx.stats.limb) / limb_fade_px, 0, 1) if limb_fade_px > 0 else 1.0
    f = 1 + (p['factor'] - 1) * w * t
    out = lab.copy()
    for k in range(1, p['passes'] + 1):              # a pass does not change hue: pass k multiplies by f once more
        out[..., 1], out[..., 2] = a * f ** k, b * f ** k
        if on_pass:
            on_pass(k, out)
    if p['passes'] == 0:
        out[..., 1], out[..., 2] = a, b
    return out


register(Block('saturation', ('colour', 'final'), {
    'factor': Param('float', 2.0, 0, 8, lo_open=True),
    'passes': Param('int', 2, 0, 5),
    'lunar_hue': Param('bool', True),
    'chroma_blur_px': Param('float', 0.0, 0, 20)},
    _saturation_apply, lambda p: math.ceil(4 * p['chroma_blur_px']) + 2, domain='lab', fades_itself=True))
