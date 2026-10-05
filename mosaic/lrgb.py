"""Scale-aware registration and bounded-memory, non-destructive Lab LRGB rendering.

Coordinates are pixel centres in native mosaic pixels. Match maps RGB -> L;
the view rotation applies to both. Previews never replace the native TIFFs.
"""
from pathlib import Path
import json
import math
import os
import struct
import tempfile
import zlib
import cv2
import numpy as np
import tifffile
from filters import layer_noise
import sharpen

# Single source of truth for the JPEG ceiling: the server enforces it in Scene.export(),
# and /api/export-preview publishes it so the UI can warn before the click.
JPEG_MAX_SIDE = 65000
JPEG_MAX_PIXELS = 80_000_000


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
                if self.raw.ndim == 3 and self.raw.shape[0] == 3 and self.raw.shape[-1] not in (1, 3):
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
        if self.raw.ndim not in (2, 3) or (self.raw.ndim == 3 and self.raw.shape[2] not in (1, 3)):
            raise ValueError('Use a mono image or an interleaved RGB image.')
        self.h, self.w = self.raw.shape[:2]
        if min(self.h, self.w) < 8:
            raise ValueError('Image is too small.')
        if not ((self.raw.dtype.kind == 'u' and self.raw.dtype.itemsize in (1, 2)) or
                (self.raw.dtype.kind == 'f' and self.raw.dtype.itemsize in (4, 8))):
            raise ValueError('Use unsigned 8/16-bit images or floats scaled to 0–1.')

    def pixels(self, raw):
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

    def warp(self, matrix, size, processor=None, margin=0):
        """Sample only the source region needed by this output strip."""
        w, h = size
        inv = np.linalg.inv(matrix)
        corners = np.array([[0, 0, 1], [w-1, 0, 1], [0, h-1, 1], [w-1, h-1, 1]]) @ inv.T
        x0, y0 = np.floor(corners[:, :2].min(axis=0) - 3-margin).astype(int)
        x1, y1 = np.ceil(corners[:, :2].max(axis=0) + 4+margin).astype(int)
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(self.w, x1), min(self.h, y1)
        if x1 <= x0 or y1 <= y0:
            return np.zeros((h, w, 3), np.float32), np.zeros((h, w), np.float32)
        step = 1 if processor else max(1, int(1/max(np.hypot(matrix[0, 0], matrix[1, 0]), 1e-9)))
        # Every tile samples the same native grid; a tile-specific phase creates seams.
        x0, y0 = x0//step*step, y0//step*step
        a = self.pixels(self.raw[y0:y1:step, x0:x1:step])
        if processor:
            a = processor(a)
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
        mask = cv2.remap(np.ones(a.shape[:2], np.float32), mx, my, cv2.INTER_LINEAR)
        return rgb, mask


def gray8(rgb):
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    good = gray[gray > .01]
    if not good.size:
        raise ValueError('The mosaic contains no usable signal.')
    lo, hi = np.percentile(good, [1, 99.5])
    return np.uint8(np.clip((gray-lo) / max(hi-lo, .02), 0, 1) * 255)


def similarity(src, dst, threshold):
    m, inliers = cv2.estimateAffinePartial2D(np.float32(src), np.float32(dst),
                                          method=cv2.RANSAC, ransacReprojThreshold=threshold,
                                          maxIters=10000, confidence=.999, refineIters=30)
    if m is None:
        raise ValueError('No reliable scale/rotation match. Try manual control points.')
    return np.vstack([m, [0, 0, 1]]), inliers.ravel().astype(bool)


def register(l_path, rgb_path, scale_prior=None):
    l, rgb = Image(l_path), Image(rgb_path)
    lp, ls = l.preview(2400)
    rp, rs = rgb.preview(2400)
    sift = cv2.SIFT_create(nfeatures=16000, contrastThreshold=.015)
    kl, dl = sift.detectAndCompute(gray8(lp), None)
    kr, dr = sift.detectAndCompute(gray8(rp), None)
    if dl is None or dr is None:
        raise ValueError('Not enough lunar detail to match these mosaics.')
    matcher = cv2.BFMatcher(cv2.NORM_L2)
    pairs = matcher.knnMatch(dr, dl, k=2)
    reverse = {p[0].queryIdx: p[0].trainIdx for p in matcher.knnMatch(dl, dr, k=2)
               if len(p) == 2 and p[0].distance < .75*p[1].distance}
    good = [p[0] for p in pairs if len(p) == 2 and p[0].distance < .75*p[1].distance
            and reverse.get(p[0].trainIdx) == p[0].queryIdx]
    if len(good) < 12:
        raise ValueError(f'Only {len(good)} distinctive mutual matches. Use manual control points or more overlap.')
    src = np.array([kr[p.queryIdx].pt for p in good], float)
    dst = np.array([kl[p.trainIdx].pt for p in good], float)
    m, keep = similarity(src, dst, 2.5)
    if keep.sum() < 12 or keep.mean() < .25:
        raise ValueError('The feature match is ambiguous. Use manual control points.')
    hull = cv2.contourArea(cv2.convexHull(dst[keep].astype(np.float32)))
    if hull < .015*lp.shape[0]*lp.shape[1]:
        raise ValueError('Matches are concentrated in one small area. Use points spread across the Moon.')
    native = ls @ m @ np.linalg.inv(rs)
    scale = float(np.hypot(native[0, 0], native[1, 0]))
    residual = np.linalg.norm((np.c_[src[keep], np.ones(keep.sum())] @ m.T)[:, :2]-dst[keep], axis=1)*ls[0, 0]
    points = np.c_[dst[keep]*ls[0, 0], residual].tolist()
    return dict(matrix=native.tolist(), method='features', scale=scale,
                rotation=float(np.degrees(np.arctan2(native[1, 0], native[0, 0]))),
                inliers=int(keep.sum()), candidates=len(good),
                median=float(np.median(residual)), p95=float(np.percentile(residual, 95)),
                worst=float(residual.max()), points=points,
                scale_prior=scale_prior,
                warning='Residuals describe fitted feature points. Unmeasured areas may differ; inspect the overlay.')


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


class Scene:
    def __init__(self, project, match=None):
        self.project = project
        self.l = Image(project.path(project.data['sets']['L']['mosaic']))
        s = project.data['sets']['RGB']
        self.rgb = Image(project.path(s['mosaic'])) if s.get('mosaic') else None
        match = match if match is not None else project.data.get('match')
        self.match_meta = match
        self.match = np.array(match['matrix'], float) if match and self.rgb else None
        self.labels = {}
        self.noise = {}
        self.sample_light = None
        self.sharp_stats = {}
        for key in ('L', 'RGB'):
            mosaic = project.data['sets'][key].get('mosaic')
            if mosaic:
                label = project.path(str(Path(mosaic).with_suffix('')) + '_labels.png')
                if label.exists():
                    self.labels[key] = cv2.imread(str(label), cv2.IMREAD_UNCHANGED)

    def layer(self, key, matrix, size, processor=None, margin=0):
        source = self.l if key == 'L' else self.rgb
        rgb, mask = source.warp(matrix, size, processor, margin)
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

    def noise_levels(self, kernel):
        if self.sample_light is None:
            proxy, spacing = self.l.preview(512)
            gray = cv2.cvtColor(proxy, cv2.COLOR_RGB2GRAY)
            yy, xx = np.nonzero(gray > max(.02, float(np.percentile(gray, 30))))
            cx = int(np.median(xx)*spacing[0, 0]) if len(xx) else self.l.w//2
            cy = int(np.median(yy)*spacing[1, 1]) if len(yy) else self.l.h//2
            x, y = max(0, min(self.l.w-512, cx-256)), max(0, min(self.l.h-512, cy-256))
            crop = self.l.pixels(self.l.raw[y:y+512, x:x+512])
            self.sample_light = sharpen.lightness(crop)
        if kernel not in self.noise:
            self.noise[kernel] = layer_noise(self.sample_light, 4, kernel)
        return self.noise[kernel]

    def sharpening_stats(self, edits):
        if not sharpen.enabled(edits):
            return dict(enabled=False, fine_variation=1.)
        keys = ('wavelets', 'wavelet_kernel', 'wavelet_denoise', 'usm_amount', 'usm_radius', 'usm_threshold')
        key = json.dumps({k: edits[k] for k in keys}, sort_keys=True)
        if key not in self.sharp_stats:
            noise = self.noise_levels(edits['wavelet_kernel'])
            processed = sharpen.apply(self.sample_light, edits, noise)
            ratio = sharpen.fine_variation(self.sample_light, processed, edits['wavelet_kernel'])
            if len(self.sharp_stats) >= 8:
                self.sharp_stats.clear()
            self.sharp_stats[key] = dict(enabled=True, fine_variation=ratio,
                                        sample_width=self.sample_light.shape[1], sample_height=self.sample_light.shape[0])
        return self.sharp_stats[key]

    def geometry(self, edits, factor=1):
        w, h = self.l.w, self.l.h
        r = cv2.getRotationMatrix2D(((w-1)/2, (h-1)/2), edits['rotation'], 1)
        if edits['mirror']:
            center = np.array([(w-1)/2, (h-1)/2])
            r[:, :2] = np.diag([-1., 1.]) @ r[:, :2]
            r[:, 2] = center-r[:, :2] @ center
        m = np.vstack([r, [0, 0, 1]])
        corners = np.array([[-.5, -.5, 1], [w-.5, -.5, 1], [-.5, h-.5, 1], [w-.5, h-.5, 1]]) @ m.T
        lo = np.floor(corners[:, :2].min(0)+.5)
        hi = np.ceil(corners[:, :2].max(0)+.5)
        m[:2, 2] -= lo
        w, h = (hi-lo).astype(int)
        bbox = (0, 0, int(w), int(h))
        if edits['coverage'] == 'common':
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
        m = np.diag([factor, factor, 1.]) @ m
        return m, (max(1, round(w*factor)), max(1, round(h*factor))), bbox

    def render(self, matrix, size, edits, view='composite', precise=True):
        empty = False
        if view == 'composite':
            if not edits['l_visible']:
                empty = not edits['rgb_visible'] or self.match is None
                view = 'colour'
            elif not edits['rgb_visible']:
                view = 'luminance'
        active = sharpen.enabled(edits) and view in ('composite', 'luminance')
        noise = self.noise_levels(edits['wavelet_kernel']) if active else None
        processor = (lambda rgb: sharpen.rgb_from_light(sharpen.apply(sharpen.lightness(rgb), edits, noise))) if active and precise else None
        l, lm = self.layer('L', matrix, size, processor, sharpen.margin(edits) if processor else 0)
        llab = cv2.cvtColor(l, cv2.COLOR_RGB2LAB)
        light = llab[..., 0]/100
        if active and not precise:
            light = sharpen.apply(light, edits, noise, float(np.hypot(matrix[0, 0], matrix[1, 0])))
        # A shoulder and an S curve, evaluated on L only.
        k = edits['highlights']*2
        if k:
            light = np.where(light > .5, .5+(light-.5)/(1+k*(light-.5)), light)
        light = np.clip(light + edits['contrast']*(light-.5)*light*(1-light)*2, 0, 1)
        lab = np.zeros_like(llab)
        lab[..., 0] = light*100
        cm = np.zeros(size[::-1], np.float32)
        if self.match is not None:
            rgb, cm = self.layer('RGB', matrix @ self.match, size)
            clab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
            sigma = edits['smoothing']*float(np.hypot(matrix[0, 0], matrix[1, 0]))
            chroma = clab[..., 1:]
            if sigma > .05:
                weight = cv2.GaussianBlur(cm, (0, 0), sigma)
                chroma = cv2.GaussianBlur(chroma*cm[..., None], (0, 0), sigma)/np.maximum(weight[..., None], 1e-6)
            lab[..., 1:] = chroma*(edits['strength']*edits['saturation'])*np.minimum(cm, lm)[..., None]
            if view == 'colour':
                lab[..., 0] = clab[..., 0]
        if view == 'luminance':
            lab[..., 1:] = 0
        result = np.clip(cv2.cvtColor(lab, cv2.COLOR_LAB2RGB), 0, 1)
        result *= lm[..., None]
        if empty:
            result[:] = 0
        if view == 'overlay' and self.match is not None:
            result = np.stack([l[..., 0], rgb[..., 1], rgb[..., 2]], axis=-1)
        elif view == 'coverage':
            result = result*.35
            result[(lm > .5) & (cm <= .5)] = (.9, .53, .28)
            result[(lm > .5) & (cm > .5)] += (.05, .23, .19)
        elif view == 'errors':
            # Measured points only; all other areas stay dim/unknown.
            result *= .3
            if self.match_meta:
                diagnostics = self.match_meta.get('diagnostics')
                if diagnostics:
                    points = [(s['x'], s['y'], s['residual']) for s in diagnostics['samples'] if s['state'] == 'measured']
                    for sample in diagnostics['samples']:
                        if sample['state'] == 'unknown':
                            p = matrix @ [sample['x'], sample['y'], 1]
                            cv2.circle(result, tuple(np.round(p[:2]).astype(int)), 5, (.5, .53, .58), 1)
                else:
                    points = self.match_meta['points']
                for x, y, residual in points:
                    p = matrix @ [x, y, 1]
                    colour = (.22, .8, .65) if residual < 1 else (.96, .65, .26) if residual < 3 else (.94, .35, .33)
                    cv2.circle(result, tuple(np.round(p[:2]).astype(int)), 5, colour, -1)
        valid = lm > .5
        missing = float(np.mean(cm[valid] <= .5))*100 if valid.any() else 100
        return result, dict(missing_colour=missing, matched=self.match is not None)

    def preview(self, edits, view='composite'):
        matrix, size, _ = self.geometry(edits)
        scale = min(1, 1400/max(size))
        matrix = np.diag([scale, scale, 1]) @ matrix
        small = (max(1, round(size[0]*scale)), max(1, round(size[1]*scale)))
        rgb, info = self.render(matrix, small, edits, view, precise=False)
        info.update(width=size[0], height=size[1], preview_scale=scale)
        info['sharpening'] = self.sharpening_stats(edits)
        return rgb, info

    def export(self, output, edits, factor=1, layer='composite', progress=lambda x: None, cancelled=lambda: False):
        output = Path(output).expanduser().resolve()
        if output.exists():
            raise ValueError('The export file already exists. Choose another name.')
        protected = {Path(p) for key in ('L', 'RGB') for p in self.project.input_paths(key)}
        protected.update(self.project.path(s['mosaic']) for s in self.project.data['sets'].values() if s.get('mosaic'))
        if output in protected or output.is_relative_to(self.project.folder / 'derived'):
            raise ValueError('Choose an export outside the built mosaics and source files.')
        if factor not in (1, .5, .25):
            raise ValueError('Export scale must be 1, 1/2 or 1/4.')
        matrix, (w, h), _ = self.geometry(edits, factor)
        suffix = output.suffix.lower()
        if suffix not in ('.tif', '.tiff', '.png', '.jpg', '.jpeg'):
            raise ValueError('Choose TIFF, PNG or JPEG.')
        if suffix in ('.jpg', '.jpeg') and (max(w, h) > JPEG_MAX_SIDE or w*h > JPEG_MAX_PIXELS):
            raise ValueError('This JPEG is too large. Choose a smaller scale or TIFF/PNG.')
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
            halo = math.ceil(edits['smoothing']*factor*4)+3
            # Keep working arrays bounded by output width rather than image height.
            rows = max(1, min(256, int(256*factor), 2_000_000//w))
            tile_width = max(32, min(1024, int(768*factor))) if sharpen.enabled(edits) else 1024
            for y in range(0, h, rows):
                if cancelled():
                    raise InterruptedError('Export cancelled; no partial image published.')
                y0, y1 = max(0, y-halo), min(h, y+rows+halo)
                pixels = np.empty((min(rows, h-y), w, 3), np.uint16)
                for x in range(0, w, tile_width):
                    x0, x1 = max(0, x-halo), min(w, x+tile_width+halo)
                    shift = np.array([[1, 0, -x0], [0, 1, -y0], [0, 0, 1]])
                    rgb, _ = self.render(shift @ matrix, (x1-x0, y1-y0), edits, layer)
                    tile = rgb[y-y0:min(rows, h-y)+y-y0, x-x0:min(tile_width, w-x)+x-x0]
                    pixels[:, x:x+tile.shape[1]] = np.round(tile*65535).astype(np.uint16)
                if suffix in ('.png',):
                    for row in pixels:
                        data = compressor.compress(b'\0'+row.astype('>u2').tobytes())
                        if data:
                            chunk(b'IDAT', data)
                else:
                    dest[y:y+len(pixels)] = pixels if dest.dtype == np.uint16 else np.round(pixels.astype(np.float32)/257).astype(np.uint8)
                progress((y+len(pixels))/h)
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
