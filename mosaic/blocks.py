"""Chain blocks (plan Appendix A): parameter schemas, statistics, readouts and the pixel operations.

Phase 2 has the five operations lunar_finish already had — wavelets, unsharp mask, highlights, contrast and
saturation — reproducing it (golden fixtures). A block works on float32 RGB in [0, 1], or on CIE Lab (L* 0–100)
when its domain is "lab"; chain.run_chain gives adjacent Lab blocks one conversion (plan §4.3, D17).

Statistics come from chain.prepare, never from inside apply() (D1): a block's `prepare(kind, x, params, ctx)`
sees the proxy ("proxy") or the full-resolution probe ("probe") as it arrives at the block, in its domain.
"""
import copy
import math

import cv2
import numpy as np

import deconv
import filters as F
import measurements as ms

PATHS = ('luminance', 'colour', 'final')


class Param:
    """One parameter: kind is float, int, bool, choice or floats (a list of floats, `length` = (min, max))."""

    def __init__(self, kind, default, lo=None, hi=None, lo_open=False, hi_open=False, choices=(), length=None,
                 validator=None):
        self.kind, self.default, self.lo, self.hi = kind, default, lo, hi
        self.lo_open, self.hi_open, self.choices, self.length = lo_open, hi_open, tuple(choices), length
        self.validator = validator                     # kind 'custom': validator(name, value) -> value

    def check(self, name, v):
        """The value, typed, or raises ValueError with a sentence naming the parameter."""
        if self.kind == 'custom':
            return self.validator(name, v)
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
                 fades_itself=False, check=None):
        self.type, self.paths, self.params, self._apply, self._margin = type, paths, params, apply, margin
        self._domain, self.detail, self._prepare, self.fades_itself = domain, detail, prepare, fades_itself
        self._check = check                            # check(params) raises ValueError for bad combinations

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
        out = {k: spec.check(k, params.get(k, copy.deepcopy(spec.default))) for k, spec in self.params.items()}
        if self._check:
            self._check(out)
        return out


BLOCKS = {}


def register(block):
    BLOCKS[block.type] = block
    return block


# ---------------------------------------------------------------- wavelets (detail, RGB)

def _wavelets_prepare(kind, x, p, ctx):
    if kind != 'probe':
        return {}
    # layer noise from the full-resolution probe, on stored values, as the layers are sharpened; and the highest
    # frequency with real signal there, which says which layer is worth pushing (DESIGN.md §7)
    gray = x.mean(axis=2)
    c = ms.cutoff(gray)
    return {'noise': F.layer_noise(gray, len(p['biases']), p['kernel']), 'cutoff': c, 'suggested_layer': ms.suggest_layer(c)}


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


# ---------------------------------------------------------------- levels and curves (tone, RGB)

def _levels_check(p):
    if p['white'] <= p['black']:
        raise ValueError('The white point must be above the black point.')


def _levels_apply(x, p, st, ctx):
    b, w, g = p['black'] / 255, p['white'] / 255, p['gamma']
    ob, ow = p['out_black'] / 255, p['out_white'] / 255
    return ob + (ow - ob) * np.clip((x - b) / (w - b), 0, 1) ** (1 / g)


register(Block('levels', PATHS, {
    'black': Param('float', 0.0, 0, 254), 'white': Param('float', 255.0, 1, 255),
    'gamma': Param('float', 1.0, 0.1, 9.99),
    'out_black': Param('float', 0.0, 0, 255), 'out_white': Param('float', 255.0, 0, 255)},
    _levels_apply, lambda p: 0, check=_levels_check))


def _points(name, v):
    """Curve control points in levels: 2–16 [in, out] pairs, `in` strictly increasing, all within 0–255."""
    if not isinstance(v, (list, tuple)) or not 2 <= len(v) <= 16:
        raise ValueError(f'{name} needs 2 to 16 points.')
    pts = []
    for pt in v:
        if (not isinstance(pt, (list, tuple)) or len(pt) != 2 or
                any(isinstance(c, bool) or not isinstance(c, (int, float)) or not 0 <= c <= 255 for c in pt)):
            raise ValueError(f'Each of the {name} is an [input, output] pair of levels from 0 to 255.')
        pts.append([float(pt[0]), float(pt[1])])
    if any(b[0] <= a[0] for a, b in zip(pts, pts[1:])):
        raise ValueError(f'The {name} need strictly increasing input levels.')
    return pts


def natural_spline(xs, ys):
    """A natural cubic spline through the points (second derivative 0 at both ends), flat beyond the end points."""
    xs, ys = np.asarray(xs, float), np.asarray(ys, float)
    n, h = len(xs), np.diff(xs)
    M = np.zeros(n)
    if n > 2:
        A = np.zeros((n - 2, n - 2))
        r = 6 * ((ys[2:] - ys[1:-1]) / h[1:] - (ys[1:-1] - ys[:-2]) / h[:-1])
        for i in range(n - 2):
            A[i, i] = 2 * (h[i] + h[i + 1])
            if i:
                A[i, i - 1] = h[i]
            if i < n - 3:
                A[i, i + 1] = h[i + 1]
        M[1:-1] = np.linalg.solve(A, r)

    def f(x):
        x = np.clip(np.asarray(x, float), xs[0], xs[-1])
        i = np.clip(np.searchsorted(xs, x) - 1, 0, n - 2)
        t, hi = x - xs[i], h[i]
        return (ys[i] + ((ys[i + 1] - ys[i]) / hi - hi * (2 * M[i] + M[i + 1]) / 6) * t + M[i] / 2 * t * t
                + (M[i + 1] - M[i]) / (6 * hi) * t ** 3)
    return f


_LUTS = {}


def curve_lut(points):
    """The curve on a 65 536-entry grid over 0–1, clamped to 0–1 (cached per set of points)."""
    key = tuple(map(tuple, points))
    if key not in _LUTS:
        if len(_LUTS) > 64:
            _LUTS.clear()
        grid = np.linspace(0, 1, 65536)
        xs, ys = np.array(points).T
        _LUTS[key] = (grid, np.clip(natural_spline(xs, ys)(grid * 255) / 255, 0, 1))
    return _LUTS[key]


def _curves_prepare(kind, x, p, ctx):
    # the histogram at this point in the chain, for drawing the curve over it
    return {'histogram': np.histogram(x.mean(axis=2), 256, (0, 1))[0].tolist()} if kind == 'proxy' else {}


def _curves_apply(x, p, st, ctx):
    grid, lut = curve_lut(p['points'])
    return np.interp(x, grid, lut).astype(np.float32)


register(Block('curves', PATHS, {
    'points': Param('custom', [[0.0, 0.0], [255.0, 255.0]], validator=_points)},
    _curves_apply, lambda p: 0, prepare=_curves_prepare))


# ---------------------------------------------------------------- Gaussian blur (detail)

def _blur_apply(x, p, st, ctx):
    sigma = p['sigma'] * ctx.scale
    return x if sigma <= 0 else cv2.GaussianBlur(x, (0, 0), sigma, borderType=cv2.BORDER_REFLECT_101)


register(Block('gaussian_blur', PATHS, {'sigma': Param('float', 1.0, 0, 50)},
               _blur_apply, lambda p: math.ceil(4 * p['sigma']) + 1, detail=True))


# ---------------------------------------------------------------- white balance (tone, RGB; D2, D3)

def _reference(name, v):
    if not isinstance(v, dict) or v.get('mode') not in ('auto', 'rect'):
        raise ValueError(f'The {name} reference is auto or a rectangle.')
    if v['mode'] == 'auto':
        return {'mode': 'auto'}
    r = v.get('rect')
    if (not isinstance(r, (list, tuple)) or len(r) != 4 or
            any(isinstance(c, bool) or not isinstance(c, (int, float)) or c < 0 for c in r) or r[2] <= 0 or r[3] <= 0):
        raise ValueError(f'The {name} rectangle is [x, y, width, height] in pixels.')
    return {'mode': 'rect', 'rect': [float(c) for c in r]}


def _false_only(name, v):
    if v is not False:
        raise ValueError('Structure detection is not available yet (it stays off).')
    return False


def _wb_check(p):
    if p['white_high'] <= p['white_low']:
        raise ValueError('The upper white limit must be above the lower one.')


def _rect_mask(rect, shape, q):
    """A rectangle in canvas px as a mask on the proxy grid. (From phase 4 rectangles are drawn in the view.)"""
    m = np.zeros(shape[:2], bool)
    x, y, w, h = (v / q for v in rect)
    m[int(y):int(np.ceil(y + h)), int(x):int(np.ceil(x + w))] = True
    return m


def _wb_prepare(kind, x, p, ctx):
    """PixInsight ColorCalibration, measured on the proxy: the background is the fixed point (DESIGN.md §5.3)."""
    if kind != 'proxy':
        return {}
    st = ctx.stats
    if p['background']['mode'] == 'rect':
        bg = _rect_mask(p['background']['rect'], x.shape, st.q)
    else:            # outside the lit disk, kept clear of the limb glow, without no-data (exact zeros)
        near = cv2.dilate(st.filled.astype(np.uint8), np.ones((7, 7), np.uint8)) > 0
        bg = ~near & (x.max(axis=2) > 0)
    B = np.median(x[bg], axis=0) if bg.any() else np.zeros(3, np.float32)
    wm = _rect_mask(p['white']['rect'], x.shape, st.q) if p['white']['mode'] == 'rect' else st.disk.copy()
    wm &= ((x >= p['white_low']) & (x <= p['white_high'])).all(axis=2)
    if not wm.any():
        return {'B': B, 'g': np.ones(3), 'note': 'No pixel lies within the white limits; the colour is unchanged.'}
    W = x[wm].mean(axis=0) - B
    if (W <= 0).any():
        return {'B': B, 'g': np.ones(3), 'note': 'The white reference is not brighter than the background.'}
    g = W.sum() / (3 * W)                  # neutral white, with the white reference's mean brightness kept (D2)
    return {'B': B, 'W': W, 'g': g, 'ratios': g / g.min(), 'pixels': int(wm.sum()),
            'note': 'PixInsight sometimes also rescales overall brightness; this block never does.'}


def _wb_apply(x, p, st, ctx):
    B, g = np.asarray(st['B'], np.float32), np.asarray(st['g'], np.float32)
    out = B + g * (x - B)
    if p['neutralize_background']:
        out = out + (p['background_target'] - B)
    return out


register(Block('white_balance', ('colour', 'final'), {
    'background': Param('custom', {'mode': 'auto'}, validator=_reference),
    'white': Param('custom', {'mode': 'auto'}, validator=_reference),
    'white_low': Param('float', 0.10, 0, 1), 'white_high': Param('float', 0.99, 0, 1),
    'structure_detection': Param('custom', False, validator=_false_only),
    'neutralize_background': Param('bool', False),
    'background_target': Param('float', 0.0, 0, 0.2)},
    _wb_apply, lambda p: 0, prepare=_wb_prepare, check=_wb_check))


# ---------------------------------------------------------------- sub-pixel RGB shift (detail, colour; D16)

def _shifts(name, v):
    if not isinstance(v, dict) or set(v) - {'r', 'b'}:
        raise ValueError('Manual shifts are {"r": [dx, dy], "b": [dx, dy]} in pixels.')
    out = {}
    for ch in ('r', 'b'):
        d = v.get(ch, [0.0, 0.0])
        if (not isinstance(d, (list, tuple)) or len(d) != 2 or
                any(isinstance(c, bool) or not isinstance(c, (int, float)) or abs(c) > 5 for c in d)):
            raise ValueError(f'The {ch} shift is [dx, dy], each within ±5 px.')
        out[ch] = [float(d[0]), float(d[1])]
    return out


def _rgbshift_prepare(kind, x, p, ctx):
    """Both estimators on up to three textured crops of the probe; a channel is corrected only when, on each axis,
    they agree within 0.15 px on at least two crops, and the shift is at least 0.1 px."""
    if kind != 'probe' or p['mode'] == 'manual':
        return {}
    size = min(512, min(x.shape[:2]) // 3)          # three non-overlapping crops fit in the probe
    boxes = ms.textured_boxes(x.mean(axis=2), 3, size)
    est = [ms.rgb_shift(x[y:y + size, x0:x0 + size]) for y, x0 in boxes]
    out = {'crops': [[int(y), int(x0), size] for y, x0 in boxes],
           'estimates': {ch: [[list(map(float, e[ch][0])), list(map(float, e[ch][1]))] for e in est] for ch in 'rb'}}
    for ch in ('r', 'b'):
        if len(est) < 2:
            out[ch] = {'apply': [0.0, 0.0], 'why': 'fewer than two crops with enough texture'}
            continue
        mean = []
        for axis in (0, 1):
            agree = [(e[ch][0][axis] + e[ch][1][axis]) / 2 for e in est if abs(e[ch][0][axis] - e[ch][1][axis]) <= 0.15]
            mean.append(float(np.mean(agree)) if len(agree) >= 2 else None)
        if None in mean:
            out[ch] = {'apply': [0.0, 0.0], 'why': 'the two estimators disagree'}
        elif np.hypot(*mean) < 0.1:
            out[ch] = {'apply': [0.0, 0.0], 'why': f'only {np.hypot(*mean):.2f} px off: nothing to correct'}
        else:
            out[ch] = {'apply': [-mean[0], -mean[1]], 'why': f'{np.hypot(*mean):.2f} px corrected'}
    return out


def _rgbshift_apply(x, p, st, ctx):
    out = x.copy()
    for ch, k in (('r', 0), ('b', 2)):
        dx, dy = p['manual'][ch] if p['mode'] == 'manual' else st.get(ch, {}).get('apply', [0.0, 0.0])
        if dx or dy:
            dx, dy = dx * ctx.scale, dy * ctx.scale
            out[..., k] = cv2.warpAffine(x[..., k], np.float32([[1, 0, dx], [0, 1, dy]]), x.shape[1::-1],
                                         flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REFLECT)
    return out


register(Block('rgb_shift', ('colour',), {
    'mode': Param('choice', 'auto', choices=('auto', 'manual')),
    'manual': Param('custom', {'r': [0.0, 0.0], 'b': [0.0, 0.0]}, validator=_shifts)},
    _rgbshift_apply, lambda p: 9, detail=True, prepare=_rgbshift_prepare))


# ---------------------------------------------------------------- deconvolution (detail; plan Appendix C)

def _psf(name, v):
    if not isinstance(v, dict) or v.get('kind') not in ('synthetic', 'file'):
        raise ValueError('The PSF is synthetic (LuckyStackWorker\'s three sliders) or an image file.')
    if v['kind'] == 'file':
        if not isinstance(v.get('path'), str) or not v['path']:
            raise ValueError('A PSF image needs its file path.')
        return {'kind': 'file', 'path': v['path']}
    spec = {'airy_radius': Param('float', 3.0, 1, 100), 'diffraction_intensity': Param('float', 100.0, 10, 200),
            'seeing_index': Param('float', 3.0, 1, 5)}
    unknown = set(v) - set(spec) - {'kind'}
    if unknown:
        raise ValueError(f'Unknown PSF setting: {", ".join(sorted(unknown))}.')
    return dict({'kind': 'synthetic'}, **{k: s.check(k.replace('_', ' '), v.get(k, s.default)) for k, s in spec.items()})


def _deconv_prepare(kind, x, p, ctx):
    if kind != 'probe':
        return {}
    mono = bool(np.abs(x - x[..., 1:2]).max() < 2e-3)
    q = p['psf']
    psfs = (deconv.file_psf(q['path']) if q['kind'] == 'file' else
            deconv.synthetic_psf(q['airy_radius'], q['diffraction_intensity'], q['seeing_index'], mono))
    out = {'psfs': psfs, 'mono': mono, 'dering_threshold': float(np.percentile(deconv.gradient(x.mean(axis=2)), 99.5))}
    if q['kind'] == 'synthetic':
        out['first_dark_ring_px'] = [deconv.first_dark_ring(q['airy_radius'], 532.0 if mono else deconv.WAVELENGTHS[c])
                                     for c in 'rgb']
    return out


def _deconv_apply(x, p, st, ctx):
    out = np.dstack([deconv.wpl(x[..., c], st['psfs'][c], p['iterations']) for c in range(3)])
    if p['dering_strength'] > 0:
        m = (p['dering_strength'] * deconv.dering_mask(x.mean(axis=2), st['dering_threshold'], p['dering_radius']))[..., None]
        out = out * (1 - m) + x * m
    if p['blend_raw'] > 0:
        out = (1 - p['blend_raw']) * out + p['blend_raw'] * x
    return out


register(Block('deconvolution', PATHS, {
    'psf': Param('custom', {'kind': 'synthetic', 'airy_radius': 3.0, 'diffraction_intensity': 100.0, 'seeing_index': 3.0},
                 validator=_psf),
    'iterations': Param('int', 10, 1, 100),
    'dering_strength': Param('float', 0.0, 0, 1), 'dering_radius': Param('float', 3.0, 1, 20),
    'blend_raw': Param('float', 0.0, 0, 1)},
    _deconv_apply, lambda p: deconv.MARGIN, detail=True, prepare=_deconv_prepare))
