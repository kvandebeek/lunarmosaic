"""Numerical filters shared by the finishing CLI, the application and (from phase 2) the chain blocks.

Moved verbatim from lunar_finish.py in phase 1 of mosaic/docs/IMPLEMENTATION_PLAN.md: the à-trous wavelets with
PixInsight's measured bias law, Photoshop's measured Unsharp Mask, the guided filter, the highlight shoulder,
the S-curve and the lunar hue weighting. Pure functions on float32 arrays; no I/O, no settings.
"""
import cv2
import numpy as np

LUNAR_BANDS = ((45, 115), (225, 295))      # orange–yellow and blue hue bands (Lab hue angle, degrees)


def guided_self(I, r, eps):
    """Self-guided filter (He et al.): smoothing that keeps strong edges (limb, crater rims) sharp."""
    k = (2 * r + 1, 2 * r + 1)
    mean = lambda x: cv2.boxFilter(x, -1, k, normalize=True, borderType=cv2.BORDER_REFLECT)
    mI = mean(I)
    var = mean(I * I) - mI * mI
    a = var / (var + eps)
    b = mI - a * mI
    return mean(a), mean(b)


def shoulder(x, knee, top, h):
    """Soft highlight roll-off: identity below knee; the knee-to-top span is lowered by a fraction h at the top.
    Smooth (slope 1 at the knee), monotonic for h < 0.5, and continues with slope 1 - 2h above top."""
    span = max(top - knee, 1e-6)
    u = np.clip((x - knee) / span, 0, None)
    y = np.where(u <= 1, x - h * span * u * u, (top - h * span) + (1 - 2 * h) * (x - top))
    return np.where(x > knee, y, x)


def s_curve(x, m, c):
    """Very gentle S-curve pivoting at m: 0 -> 0, m -> m, 1 -> 1; midtone slope 1 + c."""
    lo = np.clip(x / max(m, 1e-6) - 1, -1, 0)
    hi = np.clip((x - m) / max(1 - m, 1e-6), 0, 1)
    y_lo = m * (1 + lo + c * lo * (1 - lo * lo))
    y_hi = m + (1 - m) * (hi + c * hi * (1 - hi * hi))
    return np.where(x <= m, y_lo, y_hi)

KERNELS = {"linear": np.array([1, 2, 1], np.float32) / 4,          # PixInsight "Linear Interpolation (3)"
           "b3": np.array([1, 4, 6, 4, 1], np.float32) / 16}     # PixInsight "B3 Spline (5)"


def atrous(L, n, kernel="linear"):
    """À-trous wavelet layers (finest first, dyadic scales 1, 2, 4, … px) and the smooth residual;
    L = sum(layers) + residual."""
    k0 = KERNELS[kernel]
    layers, c = [], L.astype(np.float32)
    for k in range(n):
        s = 2 ** k
        ker = np.zeros((len(k0) - 1) * s + 1, np.float32)
        ker[::s] = k0
        c2 = cv2.sepFilter2D(c, -1, ker, ker, borderType=cv2.BORDER_REFLECT)
        layers.append(c - c2)
        c = c2
    return layers, c


def wavelet_margin(n):
    return 2 * (2 ** n - 1) + 2          # support of the coarsest layer (B3: 2 · s per level), in px


def wavelet_gain(bias):
    """PixInsight ATrousWaveletTransform's layer gain for a bias, measured on PixInsight 1.9.5 by least-squares fits
    of its output on the input's layers: biases 0.1, 0.5, 1, 2.3, 5 give gains 2, 6, 11, 24, 51; -0.5 gives 1/6."""
    return 1 + 10 * bias if bias >= 0 else 1 / (1 + 10 * -bias)


def wavelet_sharpen(L, biases, noise, denoise, kernel="linear", gains=None):
    """Layer k is multiplied by wavelet_gain(biases[k]), or by gains[k] when the gains are given directly. With
    denoise[k] > 0 the part of each coefficient within denoise[k] × noise[k] keeps weight 1, so the grain is not
    amplified."""
    gains = [wavelet_gain(b) for b in biases] if gains is None else gains
    layers, out = atrous(L, len(gains), kernel)
    for w, g, sd, d in zip(layers, gains, noise, denoise):
        t = d * sd
        core = np.sign(w) * np.maximum(np.abs(w) - t, 0) if t > 0 else w
        out = out + w + (g - 1) * core
    return out


def usm_sigma(radius):
    """Photoshop's Gaussian for an Unsharp Mask radius, measured on Photoshop 2026 at radius 1, 2 and 4 px
    (σ = 1.080, 2.096, 4.132). The factor is held at 1 above about 10 px, beyond the measured range."""
    return radius * max(1.0, 1.080 - 0.0339 * np.log(radius))


def unsharp(x, amount, radius, threshold):
    """Photoshop's Unsharp Mask on one channel (amount as a fraction, threshold in 0–255 levels): the threshold is
    subtracted from the difference, not used as a cut-off."""
    d = x - cv2.GaussianBlur(x.astype(np.float32), (0, 0), usm_sigma(radius))
    return x + amount * np.sign(d) * np.maximum(np.abs(d) - threshold / 255.0, 0)


def layer_noise(L, n, kernel="linear", tile=32):
    """Noise level (robust σ) of each wavelet layer: the median over the 10 % flattest tiles of a crop."""
    layers, _ = atrous(L, n, kernel)
    sz = (max(1, L.shape[1] // tile), max(1, L.shape[0] // tile))
    act = cv2.resize(np.abs(layers[min(2, n - 1)]), sz, interpolation=cv2.INTER_AREA)
    lum = cv2.resize(L, sz, interpolation=cv2.INTER_AREA)
    on = lum > 0.05
    flat = on & (act <= np.percentile(act[on], 10)) if on.any() else np.ones_like(act, bool)
    return [float(np.median(cv2.resize(np.abs(w), sz, interpolation=cv2.INTER_AREA)[flat])) * 1.2533 for w in layers]


def lunar_weight(hue, floor=0.15, ramp=30.0):
    """1 on the lunar hue bands, falling (cosine) to `floor` over `ramp` degrees outside them."""
    w = np.zeros_like(hue)
    for lo, hi in LUNAR_BANDS:
        inside = ((hue - lo) % 360) <= ((hi - lo) % 360)
        d = np.minimum(np.abs(((hue - lo + 180) % 360) - 180), np.abs(((hue - hi + 180) % 360) - 180))
        wb = np.where(inside, 1.0, np.where(d < ramp, 0.5 * (1 + np.cos(np.pi * np.minimum(d, ramp) / ramp)), 0.0))
        w = np.maximum(w, wb)
    return floor + (1 - floor) * w


# ---------------------------------------------------------------- CIE Lab, exact (plan D17)

# OpenCV's constants (sRGB primaries, D65 white), so values stay comparable with cv2.cvtColor; but exact maths,
# where OpenCV's float conversion interpolates tables: its round trip is off by 66 ADU rms (p99.9 126) even on
# greys, and gives a neutral grey |a*|, |b*| up to 0.125 instead of 0. Each matrix row sums to its white point
# component, which is what makes a neutral grey come out with a* = b* = 0.
_RGB2XYZ = np.array([[0.412453, 0.357580, 0.180423],
                     [0.212671, 0.715160, 0.072169],
                     [0.019334, 0.119193, 0.950227]])
_WHITE = np.array([0.950456, 1.0, 1.088754])
_TO_T = _RGB2XYZ / _WHITE[:, None]                     # linear RGB -> (X/Xn, Y, Z/Zn)
_FROM_T = np.linalg.inv(_TO_T)
_CHUNK = 1 << 20                                         # pixels per float64 pass: ~150 MB peak at most


def _per_chunk(fn, a):
    """Apply fn to (n, 3) float64 rows in chunks; float32 out, same shape. float64 matters: in float32 the
    inverse matrix's cancellation costs saturated colours up to 0.49 ADU per round trip; in float64 0.05, the rest
    being the float32 Lab stored in between (greys, lunar colours, near-black: under 0.004 ADU)."""
    a = np.asarray(a)
    flat = a.reshape(-1, 3)
    out = np.empty(flat.shape, np.float32)
    for i in range(0, len(flat), _CHUNK):
        out[i:i + _CHUNK] = fn(flat[i:i + _CHUNK].astype(np.float64))
    return out.reshape(a.shape)


def _to_lab(v):
    lin = np.where(v <= 0.04045, v / 12.92, ((np.maximum(v, 0.04045) + 0.055) / 1.055) ** 2.4)
    f = lin @ _TO_T.T
    f = np.where(f > 0.008856, np.cbrt(f), 7.787 * f + 16 / 116)
    return np.stack([116 * f[:, 1] - 16, 500 * (f[:, 0] - f[:, 1]), 200 * (f[:, 1] - f[:, 2])], -1)


def _to_rgb(lab):
    fy = (lab[:, 0] + 16) / 116
    f = np.stack([fy + lab[:, 1] / 500, fy, fy - lab[:, 2] / 200], -1)
    lin = np.where(f > 0.206893, f ** 3, (f - 16 / 116) / 7.787) @ _FROM_T.T
    return np.where(lin <= 0.0031308, 12.92 * lin, 1.055 * np.maximum(lin, 0.0031308) ** (1 / 2.4) - 0.055)


def _lstar(v):
    lin = np.where(v <= 0.04045, v / 12.92, ((np.maximum(v, 0.04045) + 0.055) / 1.055) ** 2.4)
    y = lin @ _TO_T[1]
    return 116 * np.where(y > 0.008856, np.cbrt(y), 7.787 * y + 16 / 116) - 16


def lstar(rgb):
    """CIE L* alone, identical to rgb_to_lab's, for (..., 3) stored RGB: cheaper when a* and b* are not needed."""
    a = np.asarray(rgb)
    flat = a.reshape(-1, 3)
    out = np.empty(len(flat), np.float32)
    for i in range(0, len(flat), _CHUNK):
        out[i:i + _CHUNK] = _lstar(flat[i:i + _CHUNK].astype(np.float64))
    return out.reshape(a.shape[:-1])


def rgb_to_lab(rgb):
    """Stored (sRGB-encoded) float RGB, nominally 0–1 -> float32 CIE Lab: L* 0–100, a*, b*. Not clipped."""
    return _per_chunk(_to_lab, rgb)


def lab_to_rgb(lab):
    """float32 CIE Lab -> stored (sRGB-encoded) float RGB. Not clipped: out-of-gamut colours leave 0–1, and the
    caller clips, as with cv2.cvtColor."""
    return _per_chunk(_to_rgb, lab)


def bgr_to_lab(bgr):
    """As rgb_to_lab, for OpenCV's BGR channel order."""
    return rgb_to_lab(np.asarray(bgr)[..., ::-1])


def lab_to_bgr(lab):
    """As lab_to_rgb, returning OpenCV's BGR channel order (contiguous)."""
    return np.ascontiguousarray(lab_to_rgb(lab)[..., ::-1])
