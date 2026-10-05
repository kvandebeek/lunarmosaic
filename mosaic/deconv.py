"""Deconvolution: LuckyStackWorker 7.0.0's synthetic PSF (verified port, plan Appendix B) and WPL — Wiener-filter
preconditioned Landweber, after Wendykier's Parallel Iterative Deconvolution (plan Appendix C).

The PSF reproduces LuckyStackWorker's own code (Airy and log stages to 1e-7, blur to 7.8e-3). The WPL iteration is
this project's definition: its numerical parity with LuckyStackWorker is not established (plan phase 9, V3), so
GAMMA, TAU, the dering rule and the margin are named constants here.
"""
import cv2
import numpy as np

# ---------------------------------------------------------------- the PSF (Appendix B, verbatim port)


def bessel_j1(x):
    """J1 via Abramowitz & Stegun 9.4.4/9.4.6 polynomial approximations (|err| < 1.3e-8)."""
    x = np.asarray(x, float); ax = np.abs(x)
    small = ax < 8.0
    y = np.where(small, x*x, 0.0)
    num = x*(72362614232.0 + y*(-7895059235.0 + y*(242396853.1 + y*(-2972611.439 + y*(15704.48260 + y*(-30.16036606))))))
    den = 144725228442.0 + y*(2300535178.0 + y*(18583304.74 + y*(99447.43394 + y*(376.9991397 + y*1.0))))
    res_small = np.divide(num, den, out=np.zeros_like(num), where=den!=0)
    z = np.where(small, 1.0, 8.0/np.where(ax==0, 1.0, ax)); y2 = z*z
    xx = ax - 2.356194491
    p1 = 1.0 + y2*(0.183105e-2 + y2*(-0.3516396496e-4 + y2*(0.2457520174e-5 + y2*(-0.240337019e-6))))
    p2 = 0.04687499995 + y2*(-0.2002690873e-3 + y2*(0.8449199096e-5 + y2*(-0.88228987e-6 + y2*0.105787412e-6)))
    res_big = np.sqrt(0.636619772/np.where(ax==0, 1.0, ax))*(np.cos(xx)*p1 - z*np.sin(xx)*p2)*np.sign(x)
    return np.where(small, res_small, res_big)


SIZE = 64
WAVELENGTHS = dict(r=630.0, g=532.0, b=465.0)   # nm; all 532 when the data is mono


def _airy(r, k, radius):
    v = k * (r / radius)
    return np.where(r == 0, 1.0, (2.0 * bessel_j1(np.where(v == 0, 1e-12, v)) / np.where(v == 0, 1e-12, v)) ** 2)


def _gauss_sigma(seeing_index, airy_radius):
    return ((4.0 - (seeing_index - 1.0)) / 8.0 / 2.0) * airy_radius     # = R·(5 − S)/16


def generate_channel(wavelength_nm, airy_radius, seeing_index, diffraction_intensity, size=SIZE, blur=None):
    k = 2.0 * np.pi / (wavelength_nm / 1000.0)                          # wavelength in micrometres
    c = size / 2.0                                                      # centre at 32, not 31.5
    y, x = np.mgrid[0:size, 0:size].astype(np.float64)
    r = np.hypot(x - c, y - c)
    img = np.where(r <= airy_radius * 3.0, _airy(r, k, airy_radius), 0.0)
    if img.max() > 0:
        img = img / img.max()
    img = np.log1p(img * diffraction_intensity)                         # "diffraction intensity"
    sigma = _gauss_sigma(seeing_index, airy_radius)
    if blur is not None and sigma > 0:                                  # "seeing index"
        img = blur(img, sigma)                                          # cv2.GaussianBlur, BORDER_REPLICATE fits best
    return img, sigma


def _replicate_blur(img, sigma):
    return cv2.GaussianBlur(img, (0, 0), sigma, borderType=cv2.BORDER_REPLICATE)


def _unit(p):
    """LuckyStackWorker's min-max scaling, then unit sum. The minimum is a real floor once the PSF has no zero inside
    its frame (a large radius, or seeing blur): skipping it changes the PSF by 8.7 % at R 29, S 3 and 15 % at R 50, S 1."""
    lo, hi = float(p.min()), float(p.max())
    if hi <= lo:
        raise ValueError('The PSF is flat.')
    p = (p - lo) / (hi - lo)
    return p / p.sum()


def synthetic_psf(airy_radius, diffraction_intensity, seeing_index, mono):
    """Three unit-sum 64×64 PSFs (R, G, B): genuinely chromatic, all at 532 nm for mono data."""
    return [_unit(generate_channel(532.0 if mono else WAVELENGTHS[c], airy_radius, seeing_index, diffraction_intensity,
                                   blur=_replicate_blur)[0]) for c in 'rgb']


def file_psf(path):
    """A 16-bit grey or RGB PSF image: three unit-sum PSFs, the centre index (h//2, w//2) taken as the origin."""
    img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if img is None or img.ndim not in (2, 3):
        raise ValueError(f'Cannot read the PSF image {path}.')
    img = img.astype(np.float64)
    chans = [img] * 3 if img.ndim == 2 else [img[..., 2], img[..., 1], img[..., 0]]      # OpenCV reads BGR
    return [_unit(c) for c in chans]


def first_dark_ring(airy_radius, wavelength_nm):
    """LuckyStackWorker's 'Airy disk radius' R is not the first dark ring: with v = k·r/R and the Airy pattern's first
    zero at v = 3.8317, the ring sits at 3.8317·λ_µm/(2π) × R — 0.324 R at 532 nm."""
    return 3.8317 * (wavelength_nm / 1000.0) / (2 * np.pi) * airy_radius


# ---------------------------------------------------------------- WPL (Appendix C)

GAMMA = 1e-3        # the Wiener term of the preconditioner; not exposed
TAU = 1.0           # Landweber relaxation: |H'| ≤ 1 keeps τ = 1 stable
PAD = 64            # reflection padding inside the tile, which already carries the chain's halo
MARGIN = 128        # the halo a tile needs; T-TILE checks it at the maximum of 100 iterations, dering on


def _otf(psf, shape):
    """The PSF in an array of `shape` with its centre index rolled to (0, 0) — left in place the result shifts by
    32 px — and its real FFT."""
    k = np.zeros(shape, np.float64)
    ph, pw = psf.shape
    k[:ph, :pw] = psf
    return np.fft.rfft2(np.roll(k, (-(ph // 2), -(pw // 2)), axis=(0, 1)))


def gradient(gray):
    return np.hypot(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3))


def dering_mask(gray, threshold, radius):
    """Where ringing would show: strong edges (|∇| above the probe's 99.5th percentile, from the statistics pass —
    never measured on the tile) and near-white pixels, widened by `radius` and feathered."""
    m = ((gradient(gray) > threshold) | (gray > 0.98)).astype(np.uint8)
    k = 2 * int(round(radius)) + 1
    m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))).astype(np.float32)
    return cv2.GaussianBlur(m, (0, 0), max(radius / 2, 0.5))


def wpl(b, psf, iterations):
    """One channel, float in [0, 1]: x0 = b, then x ← clip(x + IFFT(H'·(B' − H'·X)), 0, 1), with W = H*/(|H|² + γ),
    H' = W·H and B' = W·B."""
    h, w = b.shape
    N, M = cv2.getOptimalDFTSize(h + 2 * PAD), cv2.getOptimalDFTSize(w + 2 * PAD)
    pad = cv2.copyMakeBorder(b.astype(np.float64), PAD, N - h - PAD, PAD, M - w - PAD, cv2.BORDER_REFLECT)
    H = _otf(psf, pad.shape)
    Wf = np.conj(H) / ((H * np.conj(H)).real + GAMMA)
    Hp = (Wf * H).real
    Bp = Wf * np.fft.rfft2(pad)
    x = pad
    for _ in range(iterations):
        x = np.clip(x + TAU * np.fft.irfft2(Hp * (Bp - Hp * np.fft.rfft2(x)), s=pad.shape), 0, 1)
    return x[PAD:PAD + h, PAD:PAD + w].astype(np.float32)
