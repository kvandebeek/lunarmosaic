"""Deterministic, in-memory fixtures. Images use RGB order; coordinates are pixel centres.

Disk and panel images are uint16. Calibration and spectral probes are float32 in [0, 1].
Generators use local RNGs, never write files, and return masks/coordinates as independent truth.
"""
import math

import cv2
import numpy as np


def detail(size=640, seed=924):
    """The original lrgb_workflow texture, preserving its default output exactly."""
    rng = np.random.default_rng(seed)
    texture = rng.random((size, size), dtype=np.float32)
    texture = cv2.GaussianBlur(texture, (0, 0), 2)*.5+.1
    for _ in range(350):
        x, y = rng.integers(15, size-15, 2)
        radius = int(rng.integers(2, 14))
        cv2.circle(texture, (int(x), int(y)), radius, float(rng.uniform(.15, .7)), 1)
    return np.clip(texture, 0, 1)


def lunar_disk(size=768, *, mono=False, crescent=False, seed=924):
    """Textured sphere, with an optional 120-degree phase angle and enclosed dark craters.

    `lit` includes the crater shadows; the crescent's unlit side is black but still in `disk`.
    `deep_shadows` identifies deliberately enclosed values below 1022 ADU in every channel.
    """
    if not isinstance(size, int) or size < 64:
        raise ValueError('Disk size must be an integer of at least 64 pixels.')
    radius = .43*size
    centre = (size-1)/2
    y, x = np.mgrid[:size, :size].astype(np.float32)
    x, y = (x-centre)/radius, (y-centre)/radius
    r2 = x*x+y*y
    disk = r2 <= 1
    z = np.sqrt(np.maximum(0, 1-r2))
    illumination = np.sqrt(3)/2*x-.5*z if crescent else z
    lit = disk & (illumination > 0)
    lum = np.clip(.50+1.4*(detail(size, seed)-.35), .12, .85)*(.35+.65*z)
    if crescent:
        lum *= .2+.8*np.sqrt(np.maximum(illumination, 0))
    lum[~lit] = 0
    shadows = np.zeros_like(disk)
    for cx, cy in [(-.28, -.12), (.30, .26), (.76, -.12)]:
        d = np.hypot(x-cx, y-cy)
        crater = d < .04
        if not crater.any() or not lit[d < .075].all():
            continue
        lum[(d >= .04) & (d < .065)] += .09
        lum[crater] = .006+.005*d[crater]/.04
        shadows |= crater
    rgb = lum[..., None]*np.array([.95, 1, 1.08], np.float32)
    image = np.rint(np.clip(lum if mono else rgb, 0, 1)*65535).astype(np.uint16)
    return dict(image=image, disk=disk, lit=lit, deep_shadows=shadows,
                centre_xy=(centre, centre), radius_px=radius, seed=seed)


def _starts(length, minimum, overlap):
    count = math.ceil((length-minimum)/(minimum*(1-overlap)))+1
    return np.rint(np.linspace(0, length-minimum, count)).astype(int)


def _panel_grid(width, height, k1):
    y, x = np.mgrid[:height, :width].astype(np.float32)
    cx, cy = (width-1)/2, (height-1)/2
    r2 = ((x-cx)**2+(y-cy)**2)/(max(width, height)/2)**2
    return x+k1*(x-cx)*r2, y+k1*(y-cy)*r2


def panel_coordinates(panel):
    """Map observed panel pixel centres to the original source, including radial distortion.

    The inverse sampling map is p -> c + (p-c)*(1 + k1*r^2), followed by `panel_to_canvas`.
    Positive k1 samples further from the panel centre. The matrix alone maps ideal pixels.
    """
    h, w = panel['image'].shape[:2]
    x, y = _panel_grid(w, h, panel['radial_k1'])
    m = panel['panel_to_canvas']
    return m[0, 0]*x+m[0, 2], m[1, 1]*y+m[1, 2]


def cut_panels(image, panel_px=320, *, overlap=.35, scales=(1.0,), size_variation=.15,
               radial=0.0, seed=925):
    """Overlapping crops at one or more samplings, with optional per-panel radial distortion.

    Native side lengths range from ceil(panel_px*(1-size_variation)) to panel_px. Each set
    covers the source before distortion. `radial` bounds the absolute per-panel k1.
    Coarser sets are reduced once using INTER_AREA. Rounding the reduced canvas dimensions
    changes its exact sampling: `scale_xy` and the centre-aware matrix record that truth,
    while `requested_scale` retains the requested value. No input array is modified.
    """
    image = np.asarray(image)
    if image.dtype != np.uint16 or image.ndim not in (2, 3) or (image.ndim == 3 and image.shape[2] != 3):
        raise ValueError('Panel sources must be uint16 mono or RGB images.')
    if not isinstance(panel_px, int) or panel_px < 16:
        raise ValueError('Panel size must be an integer of at least 16 pixels.')
    if not 0 <= size_variation <= .15 or not 0 < overlap < 1 or not 0 <= radial <= .1:
        raise ValueError('Use size variation 0–0.15, overlap strictly between 0 and 1, and radial 0–0.1.')
    scales = tuple(scales)
    if not scales or any(not np.isfinite(s) or s < 1 for s in scales):
        raise ValueError('Supply at least one finite sampling scale of 1 or greater.')
    h, w = image.shape[:2]
    minimum = math.ceil(panel_px*(1-size_variation))
    rng, panels = np.random.default_rng(seed), []
    for set_index, scale in enumerate(scales):
        sw, sh = round(w/scale), round(h/scale)
        if min(sw, sh) < panel_px:
            raise ValueError('Each reduced source must be at least as large as the requested panels.')
        layer = image if (sw, sh) == (w, h) else cv2.resize(image, (sw, sh), interpolation=cv2.INTER_AREA)
        sx, sy = w/sw, h/sh
        for y in _starts(sh, minimum, overlap):
            for x in _starts(sw, minimum, overlap):
                pw, ph = (int(v) for v in rng.integers(minimum, panel_px+1, 2))
                x0, y0 = int(min(x, sw-pw)), int(min(y, sh-ph))
                k1 = float(rng.uniform(-radial, radial)) if radial else 0.0
                matrix = np.array([[sx, 0, sx*x0+(sx-1)/2],
                                   [0, sy, sy*y0+(sy-1)/2], [0, 0, 1]], np.float64)
                if k1:
                    mx, my = _panel_grid(pw, ph, k1)
                    mx, my = mx+x0, my+y0
                    pixels = cv2.remap(layer, mx, my, cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_CONSTANT)
                    valid = (mx >= 0) & (mx <= sw-1) & (my >= 0) & (my <= sh-1)
                else:
                    pixels = layer[y0:y0+ph, x0:x0+pw].copy()
                    valid = np.ones((ph, pw), bool)
                panels.append(dict(id=f's{set_index}-p{len(panels):03d}', set=f's{set_index}', image=pixels,
                                   valid=valid, origin_xy=(int(x0), int(y0)), scale_xy=(sx, sy),
                                   requested_scale=float(scale), scale_rel=math.sqrt(sx*sy),
                                   panel_to_canvas=matrix, radial_k1=k1))
    return panels


def white_balance_probe(size=256, seed=926):
    """Appendix F pedestal/cast; float samples avoid 16-bit quantization in calibration checks."""
    if not isinstance(size, int) or size < 64:
        raise ValueError('Probe size must be an integer of at least 64 pixels.')
    y, x = np.mgrid[:size, :size]
    surface = np.hypot(x-(size-1)/2, y-(size-1)/2) < .35*size
    pedestal = np.array([.030, .050, .040], np.float32)
    cast = np.array([.95, 1.00, 1.08], np.float32)
    brightness = .25+.4*detail(size, seed)
    image = np.broadcast_to(pedestal, (size, size, 3)).copy()
    image[surface] += brightness[surface, None]*cast
    return dict(image=image, surface=surface, background=~surface, pedestal=pedestal, cast=cast,
                gain_ratios=np.array([1.08/.95, 1.08, 1.0]), seed=seed)


def terrain(size=1024, beta=2.5, optics_sigma=.8, seed=928):
    """Lunar-like texture, zero mean and unit rms: power falling as f^-beta, as terrain's does, then blurred by
    optics of Gaussian sigma px. Unlike band_limited_texture's flat passband, a coarser sampling of it still has a
    falling spectrum, so its information cutoff can be measured as on real panels."""
    if not isinstance(size, int) or size < 32 or not beta > 0 or not optics_sigma >= 0:
        raise ValueError('Use size >= 32, a positive beta and a nonnegative optics sigma.')
    rng = np.random.default_rng(seed)
    f = np.hypot(*np.meshgrid(np.fft.fftfreq(size), np.fft.fftfreq(size)))
    amplitude = np.where(f > 0, np.maximum(f, 1/size)**(-beta/2), 0)
    t = np.fft.ifft2(amplitude*np.exp(2j*np.pi*rng.random((size, size)))).real.astype(np.float32)
    if optics_sigma:
        t = cv2.GaussianBlur(t, (0, 0), optics_sigma)
    return (t-t.mean())/t.std()


def band_limited_texture(size=1024, cutoff=.25, *, signal_rms=.08, noise_rms=.002, seed=927):
    """Flat spectral passband through cutoff cycles/pixel, plus independent white noise.

    `signal` and `noise` are zero-mean components; image = 0.5 + signal + noise. No clipping
    or windowing is applied: either would change the known spectral support.
    """
    if not isinstance(size, int) or size < 32 or not 0 < cutoff <= .5:
        raise ValueError('Use size >= 32 and a cutoff in (0, 0.5] cycles/pixel.')
    if not np.isfinite(signal_rms) or not np.isfinite(noise_rms) or signal_rms <= 0 or noise_rms < 0:
        raise ValueError('Signal RMS must be positive and noise RMS nonnegative, both finite.')
    rng = np.random.default_rng(seed)
    f = np.fft.fftfreq(size)
    passband = np.hypot(f[:, None], f[None, :]) <= cutoff
    passband[0, 0] = False
    if not passband.any():
        raise ValueError('The cutoff must include a nonzero Fourier bin at the requested size.')
    signal = np.fft.ifft2(np.fft.fft2(rng.standard_normal((size, size)))*passband).real
    signal *= signal_rms/signal.std()
    noise = rng.standard_normal((size, size))
    noise -= noise.mean()
    noise *= noise_rms/noise.std()
    image = .5+signal+noise
    if image.min() < 0 or image.max() > 1:
        raise ValueError('The requested amplitudes exceed [0, 1]; reduce the signal or noise RMS.')
    return dict(image=image.astype(np.float32), signal=signal.astype(np.float32),
                noise=noise.astype(np.float32), cutoff=float(cutoff), seed=seed)
