"""Scale-aware registration and bounded-memory, non-destructive LRGB rendering on chains.

Coordinates are pixel centres in native mosaic pixels. The luminance mosaic's grid is the canvas: the chains run
there (plan D13), and the view — rotation, mirror, coverage crop, export scale — is applied to their result. The
colour image maps onto it through the placement's K (plan phase 6, the colour canvas) or, for a project from
before, the old global match. Previews never replace the native TIFFs.
"""
from collections import OrderedDict
from pathlib import Path
import hashlib
import json
import math
import os
import struct
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
import zlib
import cv2
import numpy as np
import tifffile
import chain

# Single source of truth for the JPEG ceiling: the server enforces it in Scene.export(),
# and /api/export-preview publishes it so the UI can warn before the click.
JPEG_MAX_SIDE = 65000
JPEG_MAX_PIXELS = 80_000_000
VIEWS = ('composite', 'luminance', 'colour', 'overlay', 'coverage', 'errors', 'resolution')
STAGE_OF = dict(composite='final', luminance='luminance', colour='colour', overlay='overlay', coverage='final',
                errors='final', resolution='final')
# the resolution view's tints, one per luminance set in the builder's set report
SET_COLOURS = ((.95, .55, .25), (.30, .65, .95), (.45, .85, .45), (.90, .40, .75), (.95, .85, .30), (.55, .45, .95))
TILE = 768            # output px per side of a view tile at full scale: bounds the canvas region a tile renders
TILE_WORKERS = max(1, min(4, os.cpu_count() or 1))     # tiles rendered at once: memory grows with each
STATS_KEEP = 4        # statistics kept per scene (each holds proxy-sized maps)
_TILES = ThreadPoolExecutor(TILE_WORKERS, thread_name_prefix='tile')


class Image:
    def __init__(self, path):
        self.path = Path(path)
        self.mapped = False
        self.invert = False
        if self.path.suffix.lower() in ('.tif', '.tiff'):
            try:
                with tifffile.TiffFile(self.path) as t:
                    photo = int(t.pages[0].photometric)
                if photo not in (0, 1, 2):
                    raise ValueError('Decode palette/other photometric TIFF normally.')
                self.raw = tifffile.memmap(self.path, mode='r')
                self.mapped = True
                self.invert = photo == 0
                if self.raw.ndim == 3 and self.raw.shape[0] in (3, 4) and self.raw.shape[-1] not in (1, 3, 4):
                    self.raw = np.moveaxis(self.raw, 0, -1)
            except (ValueError, OSError):
                self.raw = None
        else:
            self.raw = None
        if self.raw is None:
            raw = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
            if raw is None:
                raise ValueError(f'Cannot read {self.path.name}. Use TIFF, PNG or JPEG.')
            self.raw = raw if raw.ndim == 2 else raw[..., :3][..., ::-1]
        if self.raw.ndim not in (2, 3) or (self.raw.ndim == 3 and self.raw.shape[2] not in (1, 3, 4)):
            raise ValueError('Use a mono image or an interleaved RGB image.')
        # a fourth channel is coverage (the colour canvas, plan phase 6): pixels() leave it out, sample() uses it
        self.alpha = self.raw.ndim == 3 and self.raw.shape[2] == 4 and self.mapped
        self.h, self.w = self.raw.shape[:2]
        if min(self.h, self.w) < 8:
            raise ValueError('Image is too small.')
        if not ((self.raw.dtype.kind == 'u' and self.raw.dtype.itemsize in (1, 2)) or
                (self.raw.dtype.kind == 'f' and self.raw.dtype.itemsize in (4, 8))):
            raise ValueError('Use unsigned 8/16-bit images or floats scaled to 0–1.')

    def pixels(self, raw):
        if raw.ndim == 3 and raw.shape[2] == 4:
            raw = raw[..., :3]
        a = raw.astype(np.float32)
        if raw.dtype.kind == 'u' and raw.dtype.itemsize == 1:
            a /= 255
        elif raw.dtype.kind == 'u' and raw.dtype.itemsize == 2:
            a /= 65535
        elif not np.isfinite(a).all() or a.min() < 0 or a.max() > 1:
            raise ValueError('Floating point images must contain finite values scaled to 0–1.')
        if self.invert:
            a = 1-a
        if a.ndim == 2:
            a = np.repeat(a[..., None], 3, axis=2)
        elif a.shape[2] == 1:
            a = np.repeat(a, 3, axis=2)
        return a

    def preview(self, limit=1600):
        # Slice before converting: a gigapixel TIFF stays memory mapped.
        step = max(1, math.ceil(max(self.h, self.w) / limit))
        a = self.pixels(self.raw[::step, ::step])
        return a, np.diag([step, step, 1.0])

    def signature(self):
        """What identifies these pixels for a cache: path, size and modification time."""
        st = os.stat(self.path)
        return [str(self.path), st.st_size, st.st_mtime_ns]

    def proxy(self, q):
        """The 1/q INTER_AREA proxy of whole q × q blocks (pixel i covers [q·i, q·(i+1))), read in strips so a
        gigapixel mosaic is never held whole."""
        H, W = self.h // q * q, self.w // q * q
        out = np.empty((H // q, W // q, 3), np.float32)
        band = max(q, (1 << 22) // max(1, W) * q)
        for y in range(0, H, band):
            a = self.pixels(self.raw[y:min(H, y + band), :W])
            out[y // q:(y + len(a)) // q] = cv2.resize(a, (W // q, len(a) // q), interpolation=cv2.INTER_AREA)
        return out

    def sample(self, gx, gy, decimate=1):
        """Bilinear samples at source coordinates gx, gy (float64 maps, source px) and their coverage (1 inside the
        image, fading across its edge pixel), reading only the region the samples need, every `decimate`-th
        pixel. Coordinates are quantised to OpenCV's 1/32 interpolation grid before the region's origin is
        subtracted, so a sample never depends on which tile asked for it."""
        d = decimate
        qx, qy = np.rint(gx/d*32)/32, np.rint(gy/d*32)/32
        x0, y0 = max(0, int(np.floor(qx.min()))-1), max(0, int(np.floor(qy.min()))-1)
        x1, y1 = min(-(-self.w//d), int(np.floor(qx.max()))+2), min(-(-self.h//d), int(np.floor(qy.max()))+2)
        if x1 <= x0 or y1 <= y0:
            return np.zeros(gx.shape+(3,), np.float32), np.zeros(gx.shape, np.float32)
        raw = self.raw[y0*d:y1*d:d, x0*d:x1*d:d]
        a = self.pixels(raw)
        mx, my = (qx-x0).astype(np.float32), (qy-y0).astype(np.float32)
        cover = (raw[..., 3].astype(np.float32)/(65535 if raw.dtype == np.uint16 else 255) if self.alpha
                 else np.ones(a.shape[:2], np.float32))
        return cv2.remap(a, mx, my, cv2.INTER_LINEAR), cv2.remap(cover, mx, my, cv2.INTER_LINEAR)

    def warp(self, matrix, size):
        """Sample only the source region needed by this output strip."""
        w, h = size
        inv = np.linalg.inv(matrix)
        corners = np.array([[0, 0, 1], [w-1, 0, 1], [0, h-1, 1], [w-1, h-1, 1]]) @ inv.T
        x0, y0 = np.floor(corners[:, :2].min(axis=0) - 3).astype(int)
        x1, y1 = np.ceil(corners[:, :2].max(axis=0) + 4).astype(int)
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(self.w, x1), min(self.h, y1)
        if x1 <= x0 or y1 <= y0:
            return np.zeros((h, w, 3), np.float32), np.zeros((h, w), np.float32)
        step = max(1, int(1/max(np.hypot(matrix[0, 0], matrix[1, 0]), 1e-9)))
        # Every tile samples the same native grid; a tile-specific phase creates seams.
        x0, y0 = x0//step*step, y0//step*step
        raw = self.raw[y0:y1:step, x0:x1:step]
        a = self.pixels(raw)
        # Explicit inverse maps avoid warpAffine's tile-origin-dependent fixed-point
        # rounding, which can change the last covered pixel at rotated boundaries.
        xx, yy = np.arange(w, dtype=np.float64)[None, :], np.arange(h, dtype=np.float64)[:, None]
        # Quantize on OpenCV's 1/32 interpolation grid BEFORE subtracting the ROI
        # origin: float32 has different precision at 512 px and at a local 81 px.
        gx = np.rint((inv[0, 0]*xx+inv[0, 1]*yy+inv[0, 2])/step*32)/32
        gy = np.rint((inv[1, 0]*xx+inv[1, 1]*yy+inv[1, 2])/step*32)/32
        mx = (gx-x0/step).astype(np.float32)
        my = (gy-y0/step).astype(np.float32)
        rgb = cv2.remap(a, mx, my, cv2.INTER_LINEAR)
        cover = (raw[..., 3].astype(np.float32)/(65535 if raw.dtype == np.uint16 else 255) if self.alpha
                 else np.ones(a.shape[:2], np.float32))
        mask = cv2.remap(cover, mx, my, cv2.INTER_LINEAR)
        return rgb, mask


def manual_match(src, dst):
    src, dst = np.asarray(src, float), np.asarray(dst, float)
    if src.shape != dst.shape or src.ndim != 2 or src.shape[1] != 2 or len(src) < 3 or len(src) > 100:
        raise ValueError('Choose at least three corresponding points spread across both mosaics.')
    if not np.isfinite(src).all() or not np.isfinite(dst).all():
        raise ValueError('Control points must be finite.')
    if cv2.contourArea(cv2.convexHull(src.astype(np.float32))) < 10:
        raise ValueError('Control points must be spread across the image, not on a line.')
    # Manual clicks can be many native pixels apart at preview scale. Fit all
    # chosen pairs and show their residuals rather than silently dropping pairs.
    a = np.zeros((len(src)*2, 4), float)
    a[::2] = np.c_[src[:, 0], -src[:, 1], np.ones(len(src)), np.zeros(len(src))]
    a[1::2] = np.c_[src[:, 1], src[:, 0], np.zeros(len(src)), np.ones(len(src))]
    aa, bb, tx, ty = np.linalg.lstsq(a, dst.ravel(), rcond=None)[0]
    if np.hypot(aa, bb) < 1e-5:
        raise ValueError('The selected points do not define a usable scale.')
    m = np.array([[aa, -bb, tx], [bb, aa, ty], [0, 0, 1]])
    keep = np.ones(len(src), bool)
    residual = np.linalg.norm((np.c_[src, np.ones(len(src))] @ m.T)[:, :2]-dst, axis=1)
    return dict(matrix=m.tolist(), method='manual', scale=float(np.hypot(m[0, 0], m[1, 0])),
                rotation=float(np.degrees(np.arctan2(m[1, 0], m[0, 0]))),
                inliers=int(keep.sum()), candidates=len(src), median=float(np.median(residual)),
                p95=float(np.percentile(residual, 95)), worst=float(residual.max()),
                points=np.c_[dst, residual].tolist(), warning='Manual control-point fit; alignment elsewhere is unverified.')


def largest_rectangle(mask):
    """Largest entirely covered rectangle, including internal holes in the calculation."""
    heights = np.zeros(mask.shape[1], int)
    best, area = None, 0
    for y, row in enumerate(mask):
        heights = np.where(row, heights+1, 0)
        stack = []
        for x, height in enumerate(np.r_[heights, 0]):
            start = x
            while stack and stack[-1][1] > height:
                pos, h = stack.pop()
                if h*(x-pos) > area:
                    area, best = h*(x-pos), (pos, y-h+1, x, y+1)
                start = pos
            if not stack or stack[-1][1] < height:
                stack.append((start, height))
    return best




class LuminanceSource:
    """The luminance canvas as a chain source (chain.py's protocol): the L mosaic's own grid, its coverage from
    the builder's labels."""

    def __init__(self, scene):
        self.scene, self.h, self.w = scene, scene.l.h, scene.l.w

    def read(self, y0, x0, h, w, step=1):
        im = self.scene.l
        rgb = im.pixels(im.raw[y0:y0+(h-1)*step+1:step, x0:x0+(w-1)*step+1:step])
        X, Y = np.meshgrid(x0+step*np.arange(w, dtype=np.float64), y0+step*np.arange(h, dtype=np.float64))
        return rgb, self.scene.coverage('L', X, Y)

    def proxy(self, q):
        return self.scene.l.proxy(q)

    def proxy_coverage(self, q):
        c = (q-1)/2
        X, Y = np.meshgrid(c+q*np.arange(self.w//q, dtype=np.float64), c+q*np.arange(self.h//q, dtype=np.float64))
        return self.scene.coverage('L', X, Y)


class MatchedColour:
    """The colour mosaic on the luminance canvas through the legacy global match, until phase 6 places colour per
    panel. Each canvas pixel's colour-mosaic coordinate comes from one inverse of the match, so tiles, strips and
    the proxy all sample the colour mosaic alike."""

    def __init__(self, scene):
        self.scene, self.h, self.w = scene, scene.l.h, scene.l.w
        self._proxy = (None, None)

    def at(self, X, Y, step=1):
        """Colour and coverage at canvas columns X and rows Y (1-D, canvas px)."""
        sc = self.scene
        inv = np.linalg.inv(sc.match)
        X, Y = np.asarray(X, np.float64)[None, :], np.asarray(Y, np.float64)[:, None]
        gx = inv[0, 0]*X+inv[0, 1]*Y+inv[0, 2]
        gy = inv[1, 0]*X+inv[1, 1]*Y+inv[1, 2]
        scale = float(np.hypot(sc.match[0, 0], sc.match[1, 0]))      # canvas px per colour px
        rgb, cover = sc.rgb.sample(gx, gy, max(1, int(step/scale)))
        label = sc.coverage('RGB', gx, gy)
        return rgb, cover if label is None else cover*label

    def read(self, y0, x0, h, w, step=1):
        return self.at(x0+step*np.arange(w), y0+step*np.arange(h), step)

    def proxies(self, q):
        key = (q, self.scene.match.tobytes())
        if self._proxy[0] != key:
            c, rows = (q-1)/2, self.h//q
            X = c+q*np.arange(self.w//q)
            parts = [self.at(X, c+q*np.arange(y, min(rows, y+256)), q) for y in range(0, rows, 256)]
            self._proxy = (key, (np.concatenate([a for a, _ in parts]), np.concatenate([b for _, b in parts])))
        return self._proxy[1]

    def proxy(self, q):
        return self.proxies(q)[0]

    def proxy_coverage(self, q):
        return self.proxies(q)[1]


class Scene:
    """The project's mosaics, rendered through a chain document: statistics once per chain on the whole canvas
    (D1), the chains on the canvas grid, the view applied afterwards (D13)."""

    def __init__(self, project, match=None):
        self.project = project
        self.l = Image(project.path(project.stream('luminance')['mosaic']))
        s = project.stream('colour')
        self.rgb = Image(project.path(s['mosaic'])) if s.get('mosaic') else None
        match = match if match is not None else project.match
        self.match_meta = match
        self.match = np.array(match['matrix'], float) if match and self.rgb else None
        self.labels, self.covered = {}, {}
        for key, name in (('L', 'luminance'), ('RGB', 'colour')):
            mosaic = project.stream(name).get('mosaic')
            if mosaic:
                label = project.path(str(Path(mosaic).with_suffix('')) + '_labels.png')
                if label.exists():
                    self.labels[key] = cv2.imread(str(label), cv2.IMREAD_UNCHANGED)
                    self.covered[key] = (self.labels[key] != 65535).astype(np.float32)
        self.set_tint, self.set_legend, self.label_scale = self.read_sets()
        self.lum, self.col = LuminanceSource(self), MatchedColour(self)
        self._cond = threading.Condition()
        self._stats, self._running = OrderedDict(), set()
        self._base, self._base_busy = (None, None), False
        self._prefix = chain.PrefixCache()       # per block: a changed block restarts the statistics there

    def read_sets(self):
        """For the resolution view: an RGBA tint at label resolution (which luminance set supplied each region) and
        its legend, from the builder's set report; (None, [], 4) when the mosaic has none."""
        report_path = self.project.stream('luminance').get('sets_report')
        label = self.labels.get('L')
        if not report_path or label is None:
            return None, [], 4
        try:
            report = json.loads(self.project.path(report_path).read_text('utf-8'))
            order = list(report['sets'])
            index = np.array([order.index(s) for s in report['label_to_set']] + [len(order)])
        except (OSError, ValueError, KeyError, TypeError):
            return None, [], 4                 # an optional report must not stop the mosaic from opening
        lut = np.array([SET_COLOURS[i % len(SET_COLOURS)] + (1.0,) for i in range(len(order))] + [(0, 0, 0, 0)], np.float32)
        tint = lut[index[np.minimum(label, len(index) - 1)]]
        names = {x['id']: x.get('label') or x['id'] for x in self.project.data['sets']}
        canvas = self.project.stream('luminance').get('canvas_arcsec_px')
        legend = [dict(id=sid, label=names.get(sid, sid), colour=SET_COLOURS[i % len(SET_COLOURS)], tier=v.get('tier'),
                       scale_rel=v.get('scale_rel'), detail_px=v.get('detail_px'), disk_share=v.get('disk_share'),
                       # the set's own pixel scale: the canvas's (after any reduction) × the reduction × scale_rel
                       arcsec_px=canvas * report.get('canvas_scale', 1) * v.get('scale_rel', 1) if canvas else None)
                  for i, (sid, v) in enumerate(report['sets'].items())]
        return tint, legend, int(report.get('label_scale', 4))

    def layer(self, key, matrix, size):
        """One mosaic resampled through `matrix` with its coverage (alignment checks and the common crop)."""
        source = self.l if key == 'L' else self.rgb
        rgb, mask = source.warp(matrix, size)
        label = self.labels.get(key)
        if label is not None:
            # The builder crops the label grid with the output TIFF.
            to_source = np.diag([source.w/label.shape[1], source.h/label.shape[0], 1.])
            inv = np.linalg.inv(matrix @ to_source)
            xx, yy = np.arange(size[0], dtype=np.float64)[None, :], np.arange(size[1], dtype=np.float64)[:, None]
            mx = (np.rint((inv[0, 0]*xx+inv[0, 1]*yy+inv[0, 2])*32)/32).astype(np.float32)
            my = (np.rint((inv[1, 0]*xx+inv[1, 1]*yy+inv[1, 2])*32)/32).astype(np.float32)
            covered = cv2.remap((label != 65535).astype(np.float32), mx, my, cv2.INTER_LINEAR)
            mask *= covered
        return rgb, mask

    def coverage(self, key, gx, gy):
        """The builder's label coverage of mosaic `key` at its pixel coordinates gx, gy (None without labels)."""
        label = self.covered.get(key)
        if label is None:
            return None
        image = self.l if key == 'L' else self.rgb
        mx = (np.rint(gx*(label.shape[1]/image.w)*32)/32).astype(np.float32)
        my = (np.rint(gy*(label.shape[0]/image.h)*32)/32).astype(np.float32)
        return cv2.remap(label, mx, my, cv2.INTER_LINEAR)

    def colour_source(self):
        return self.col if self.match is not None else None

    # ---------------------------------------------------------------- statistics (D1; plan §4.5)

    def signature(self):
        return [self.l.signature(), self.rgb.signature() if self.rgb else None,
                None if self.match is None else self.match.tolist()]

    def base(self):
        """chain.Base for the current sources (the proxies), kept while the mosaics and the match stay the same."""
        key = json.dumps(self.signature())
        with self._cond:
            while self._base_busy:
                self._cond.wait()
            if self._base[0] == key:
                return self._base[1]
            self._base_busy = True
        base = None
        try:
            base = chain.Base(self.lum, self.colour_source())
        finally:
            with self._cond:
                self._base_busy = False
                if base is not None:
                    self._base = (key, base)
                self._cond.notify_all()
        return base

    def stats_key(self, doc):
        return hashlib.sha1(json.dumps([self.signature(), doc['protect_space'], doc['chains']],
                                       sort_keys=True).encode()).hexdigest()

    def stats(self, doc, check=None):
        """chain.prepare for this chain document, computed once: a caller asking while another computes the same
        statistics waits for them. check() may raise chain.Stale to abandon the computation (the worker)."""
        doc = chain.normalised(doc)
        key = self.stats_key(doc)
        with self._cond:
            while key not in self._stats:
                if key not in self._running:
                    self._running.add(key)
                    break
                self._cond.wait()
            else:
                self._stats.move_to_end(key)
                return self._stats[key]
        result = None
        try:
            result = chain.prepare(self.lum, doc, colour=self.colour_source(), check=check, base=self.base(),
                                   cache=self._prefix, tag=json.dumps(self.signature()))
        finally:
            with self._cond:
                self._running.discard(key)
                if result is not None:
                    self._stats[key] = result
                    while len(self._stats) > STATS_KEEP:
                        self._stats.popitem(last=False)
                self._cond.notify_all()
        return result

    # ---------------------------------------------------------------- the view (D13)

    def geometry(self, geometry, factor=1):
        """The view matrix (canvas px -> output px), the output size and the common-coverage box. Output pixel j at
        export scale f covers view pixels [j/f, (j+1)/f): its centre is view pixel (j + 1/2)/f − 1/2, so an export
        at 1/2 or 1/4 is exactly a reduced full-scale export."""
        w, h = self.l.w, self.l.h
        r = cv2.getRotationMatrix2D(((w-1)/2, (h-1)/2), geometry['rotation'], 1)
        if geometry['mirror']:
            center = np.array([(w-1)/2, (h-1)/2])
            r[:, :2] = np.diag([-1., 1.]) @ r[:, :2]
            r[:, 2] = center-r[:, :2] @ center
        m = np.vstack([r, [0, 0, 1]])
        corners = np.array([[-.5, -.5, 1], [w-.5, -.5, 1], [-.5, h-.5, 1], [w-.5, h-.5, 1]]) @ m.T
        # cos 90° is 6e-17 in floating point: unrounded, a quarter turn of a 1008 px square came out 1010 × 1009
        corners = np.round(corners, 6)
        lo = np.floor(corners[:, :2].min(0)+.5)
        hi = np.ceil(corners[:, :2].max(0)+.5)
        m[:2, 2] -= lo
        w, h = (hi-lo).astype(int)
        bbox = (0, 0, int(w), int(h))
        if geometry['coverage'] == 'common':
            if self.match is None:
                raise ValueError('Match a colour mosaic before choosing common coverage.')
            # Conservative mask cells: erosion reserves one proxy pixel at all boundaries.
            scale = min(1, 800/max(w, h))
            proxy = np.diag([scale, scale, 1.]) @ m
            size = (max(1, round(w*scale)), max(1, round(h*scale)))
            _, lm = self.layer('L', proxy, size)
            _, cm = self.layer('RGB', proxy @ self.match, size)
            mask = cv2.erode(((lm > .999) & (cm > .999)).astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
            box = largest_rectangle(mask)
            if box is None:
                raise ValueError('No usable common rectangle. Choose the full luminance canvas.')
            bbox = tuple(int(math.ceil(v/scale)) if i < 2 else int(math.floor(v/scale)) for i, v in enumerate(box))
            m[:2, 2] -= bbox[:2]
            w, h = bbox[2]-bbox[0], bbox[3]-bbox[1]
        m = np.array([[factor, 0, -(1-factor)/2], [0, factor, -(1-factor)/2], [0, 0, 1.]]) @ m
        return m, (max(1, round(w*factor)), max(1, round(h*factor))), bbox

    def missing_colour(self, geometry):
        """Share (%) of the luminance-covered view with no colour, from the coverage masks at 400 px at most."""
        if self.match is None:
            return 100.0
        matrix, (w, h), _ = self.geometry(dict(geometry, coverage='full'))
        scale = min(1, 400/max(w, h))
        proxy = np.diag([scale, scale, 1.]) @ matrix
        size = (max(1, round(w*scale)), max(1, round(h*scale)))
        _, lm = self.layer('L', proxy, size)
        _, cm = self.layer('RGB', proxy @ self.match, size)
        valid = lm > .5
        return float(np.mean(cm[valid] <= .5))*100 if valid.any() else 100.0

    def render_view(self, matrix, size, doc, view='composite', precise=True, stats=None, origin=(0, 0)):
        """Output pixels [ox, ox + w) × [oy, oy + h) of the output that `matrix` (canvas px -> output px) defines.

        The chains run on the canvas grid and their result is resampled into the output with Lanczos-4. An exact
        render (precise) below scale 1 samples an r-times finer grid, r = ⌈1/scale⌉, and reduces it with INTER_AREA;
        an approximate one (the zoomed-out workspace) runs the chains on every step-th canvas pixel instead, at
        scale 1/step. Every pixel is mapped by the one inverse of `matrix` and quantised to OpenCV's 1/32 grid in
        canvas coordinates, so a render in tiles equals a whole one."""
        if view not in VIEWS:
            raise ValueError('Unknown view.')
        stage = 'final' if view == 'overlay' and self.match is None else STAGE_OF[view]
        if stage != 'overlay':
            doc = chain.normalised(doc)
            stats = stats or self.stats(doc)
        w, h = size
        s = float(np.hypot(matrix[0, 0], matrix[1, 0]))
        r = max(1, math.ceil(1/s-1e-9)) if precise and s < 1 else 1
        step = 1 if precise else max(1, int(1/s))
        A = np.array([[r, 0, (r-1)/2], [0, r, (r-1)/2], [0, 0, 1.]])
        inv = np.linalg.inv(A @ matrix)
        ox, oy = origin
        rgb, lm, cm = np.empty((h, w, 3), np.float32), np.empty((h, w), np.float32), np.empty((h, w), np.float32)
        t = max(1, TILE//r)

        def tile(ty, tx):
            th, tw = min(t, h-ty), min(t, w-tx)
            U = r*(ox+tx)+np.arange(r*tw, dtype=np.float64)
            V = r*(oy+ty)+np.arange(r*th, dtype=np.float64)
            parts = self._tile(inv, U, V, step, stage, stats, doc, tint=view == 'resolution')
            if r > 1:
                parts = [cv2.resize(v, (tw, th), interpolation=cv2.INTER_AREA) for v in parts]
            rgb[ty:ty+th, tx:tx+tw], lm[ty:ty+th, tx:tx+tw], cm[ty:ty+th, tx:tx+tw] = parts
        jobs = [(ty, tx) for ty in range(0, h, t) for tx in range(0, w, t)]
        # tiles are independent (T-TILE): render them side by side; a tile's own work is mostly single-threaded
        if len(jobs) == 1 or threading.current_thread().name.startswith('tile'):
            for job in jobs:
                tile(*job)
        else:
            for f in [_TILES.submit(tile, *job) for job in jobs]:
                f.result()
        result = rgb
        if view == 'coverage':
            result = result*.35
            result[(lm > .5) & (cm <= .5)] = (.9, .53, .28)
            result[(lm > .5) & (cm > .5)] += (.05, .23, .19)
        elif view == 'errors':
            # Measured points only; all other areas stay dim/unknown.
            result *= .3
            to_output = np.array([[1, 0, -ox], [0, 1, -oy], [0, 0, 1.]]) @ matrix
            if self.match_meta:
                diagnostics = self.match_meta.get('diagnostics')
                if diagnostics:
                    points = [(s['x'], s['y'], s['residual']) for s in diagnostics['samples'] if s['state'] == 'measured']
                    for sample in diagnostics['samples']:
                        if sample['state'] == 'unknown':
                            p = to_output @ [sample['x'], sample['y'], 1]
                            cv2.circle(result, tuple(np.round(p[:2]).astype(int)), 5, (.5, .53, .58), 1)
                else:
                    points = self.match_meta['points']
                for x, y, residual in points:
                    p = to_output @ [x, y, 1]
                    colour = (.22, .8, .65) if residual < 1 else (.96, .65, .26) if residual < 3 else (.94, .35, .33)
                    cv2.circle(result, tuple(np.round(p[:2]).astype(int)), 5, colour, -1)
        valid = lm > .5
        missing = float(np.mean(cm[valid] <= .5))*100 if valid.any() else 100
        info = dict(missing_colour=missing, matched=self.match is not None)
        if view == 'resolution':
            info['sets'] = self.set_legend
        return result, info

    def _tile(self, inv, U, V, step, stage, stats, doc, tint=False):
        """One tile of output grid U × V: its canvas region rendered and resampled; returns (rgb × L coverage, L
        coverage, colour coverage) on that grid — multiplied before any reduction, so a reduced render is the mean
        of the full one."""
        X = inv[0, 0]*U[None, :]+inv[0, 1]*V[:, None]+inv[0, 2]
        Y = inv[1, 0]*U[None, :]+inv[1, 1]*V[:, None]+inv[1, 2]
        gx, gy = np.rint(X/step*32)/32, np.rint(Y/step*32)/32        # in units of the (decimated) canvas grid
        rows, cols = -(-self.l.h//step), -(-self.l.w//step)
        # Lanczos-4 reads floor(x) − 3 … floor(x) + 4
        i0, i1 = max(0, int(np.floor(gy.min()))-3), min(rows, int(np.floor(gy.max()))+5)
        j0, j1 = max(0, int(np.floor(gx.min()))-3), min(cols, int(np.floor(gx.max()))+5)
        if i1 <= i0 or j1 <= j0:
            return np.zeros(gx.shape+(3,), np.float32), np.zeros(gx.shape, np.float32), np.zeros(gx.shape, np.float32)
        cover = {}
        img = self._canvas(stage, stats, doc, i0*step, j0*step, i1-i0, j1-j0, step, cover)
        mx, my = (gx-j0).astype(np.float32), (gy-i0).astype(np.float32)
        # outside the canvas any value will do: the coverage below is zero there
        rgb = np.clip(cv2.remap(img, mx, my, cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REPLICATE), 0, 1)
        if tint and self.set_tint is not None:
            # label px j covers mosaic px [q·j, q·(j+1)); 38 % of the set's colour, as the builder's contribution map
            q = self.label_scale
            t = cv2.remap(self.set_tint, ((gx*step-(q-1)/2)/q).astype(np.float32), ((gy*step-(q-1)/2)/q).astype(np.float32),
                          cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT)
            a = .38*t[..., 3:]
            rgb = rgb*(1-a)+t[..., :3]*a
        ones = np.ones(img.shape[:2], np.float32)
        lm, cm = (cv2.remap(ones if cover[k] is None else cover[k], mx, my, cv2.INTER_LINEAR) for k in ('luminance', 'colour'))
        return rgb*lm[..., None], lm, cm

    def _canvas(self, stage, stats, doc, y0, x0, h, w, step, cover):
        if stage == 'overlay':                     # the unprocessed layers: luminance in red, colour in green and blue
            l, cl = self.lum.read(y0, x0, h, w, step)
            c, cc = self.col.read(y0, x0, h, w, step)
            cover.update(luminance=cl, colour=cc)
            return np.dstack([l[..., 0], c[..., 1], c[..., 2]])
        return chain.render(self.lum, stats, doc, y0, x0, h, w, colour=self.colour_source(), stage=stage, step=step,
                            coverage=cover)

    # ---------------------------------------------------------------- layers, previews, exports

    def visible(self, layers, view):
        """The layer switches (schema 4's `view`): (view to render, whether the result is empty). With luminance
        hidden the colour layer supplies lightness too; with colour hidden the result is the grey luminance."""
        empty = False
        if view == 'composite':
            if not layers['l_visible']:
                empty = not layers['rgb_visible'] or self.match is None
                view = 'colour'
            elif not layers['rgb_visible']:
                view = 'luminance'
        return view, empty

    def render(self, matrix, size, doc, layers, view='composite', precise=True, stats=None, origin=(0, 0)):
        """render_view with the layer switches applied."""
        view, empty = self.visible(layers, view)
        rgb, info = self.render_view(matrix, size, doc, view, precise, stats, origin)
        if empty:
            rgb[:] = 0
        return rgb, info

    def preview(self, doc, geometry, layers, view='composite'):
        """The whole oriented view, at most 1400 px: approximate below scale 1 (plan §4.5)."""
        matrix, size, _ = self.geometry(geometry)
        scale = min(1, 1400/max(size))
        matrix = np.diag([scale, scale, 1]) @ matrix
        small = (max(1, round(size[0]*scale)), max(1, round(size[1]*scale)))
        rgb, info = self.render(matrix, small, doc, layers, view, precise=False)
        info.update(width=size[0], height=size[1], preview_scale=scale)
        return rgb, info

    def export(self, output, doc, geometry, layers, factor=1, layer='composite', progress=lambda x: None,
               cancelled=lambda: False, stats=None):
        """Write the view at export scale `factor` (1, 1/2, 1/4), tile by tile. The chain document and its
        statistics are fixed when the export starts, so later edits never reach a running export (T-WORKER)."""
        output = Path(output).expanduser().resolve()
        if output.exists():
            raise ValueError('The export file already exists. Choose another name.')
        protected = {Path(p) for s in self.project.data['sets'] for p in self.project.input_paths(s['id'])}
        protected.update(self.project.path(self.project.stream(n)['mosaic']) for n in ('luminance', 'colour')
                         if self.project.stream(n).get('mosaic'))
        if output in protected or output.is_relative_to(self.project.folder / 'derived'):
            raise ValueError('Choose an export outside the built mosaics and source files.')
        if factor not in (1, .5, .25):
            raise ValueError('Export scale must be 1, 1/2 or 1/4.')
        matrix, (w, h), _ = self.geometry(geometry, factor)
        suffix = output.suffix.lower()
        if suffix not in ('.tif', '.tiff', '.png', '.jpg', '.jpeg'):
            raise ValueError('Choose TIFF, PNG or JPEG.')
        if suffix in ('.jpg', '.jpeg') and (max(w, h) > JPEG_MAX_SIDE or w*h > JPEG_MAX_PIXELS):
            raise ValueError('This JPEG is too large. Choose a smaller scale or TIFF/PNG.')
        view, empty = self.visible(layers, layer)
        doc = chain.normalised(doc)
        if STAGE_OF[view] != 'overlay' and stats is None:
            stats = self.stats(doc)
        output.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix='.'+output.stem+'-', suffix=suffix, dir=output.parent)
        os.close(fd)
        dest = None
        try:
            if suffix in ('.tif', '.tiff'):
                dest = tifffile.memmap(temp, shape=(h, w, 3), dtype=np.uint16, photometric='rgb', bigtiff=w*h*6 > 3_900_000_000, metadata=None)
            elif suffix in ('.jpg', '.jpeg'):
                dest = np.empty((h, w, 3), np.uint8)
            else:
                png = open(temp, 'wb')
                png.write(b'\x89PNG\r\n\x1a\n')
                def chunk(kind, payload):
                    png.write(struct.pack('>I', len(payload))+kind+payload+struct.pack('>I', zlib.crc32(kind+payload)))
                chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 16, 2, 0, 0, 0))
                compressor = zlib.compressobj(6)
            # bands of square tiles: a rotated tile's canvas region stays small, memory stays bounded by the width
            t = max(1, TILE//round(1/factor))
            for y in range(0, h, t):
                if cancelled():
                    raise InterruptedError('Export cancelled; no partial image published.')
                n = min(t, h-y)
                pixels = np.zeros((n, w, 3), np.uint16)
                if not empty:
                    rgb, _ = self.render_view(matrix, (w, n), doc, view, True, stats, origin=(0, y))
                    pixels[:] = np.round(rgb*65535).astype(np.uint16)
                if suffix in ('.png',):
                    for row in pixels:
                        data = compressor.compress(b'\0'+row.astype('>u2').tobytes())
                        if data:
                            chunk(b'IDAT', data)
                else:
                    dest[y:y+n] = pixels if dest.dtype == np.uint16 else np.round(pixels.astype(np.float32)/257).astype(np.uint8)
                progress((y+n)/h)
            if suffix == '.png':
                chunk(b'IDAT', compressor.flush())
                chunk(b'IEND', b'')
                png.close()
            elif suffix in ('.jpg', '.jpeg'):
                ok, encoded = cv2.imencode('.jpg', dest[..., ::-1], [cv2.IMWRITE_JPEG_QUALITY, 92])
                if not ok:
                    raise OSError('JPEG could not be written.')
                encoded.tofile(temp)
            else:
                dest.flush()
                del dest
                dest = None
            # Exclusive publication never overwrites a file created during export.
            os.link(temp, output)
            return dict(path=str(output), width=w, height=h, bit_depth=8 if suffix in ('.jpg', '.jpeg') else 16)
        finally:
            if 'png' in locals() and not png.closed:
                png.close()
            if dest is not None and isinstance(dest, np.memmap):
                dest.flush()
            if os.path.exists(temp):
                os.unlink(temp)
