"""L-only sharpening. Native exports reuse the shared wavelet filters (filters.py)."""
import cv2
import numpy as np
from filters import KERNELS, atrous, wavelet_margin, wavelet_sharpen


def enabled(edits):
    return any(edits['wavelets']) or edits['usm_amount'] > 0


def margin(edits):
    wave = wavelet_margin(4) if any(edits['wavelets']) else 0
    usm = int(np.ceil(4*edits['usm_radius'])) if edits['usm_amount'] else 0
    return wave+usm+3


def lightness(rgb):
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)[..., 0]/100


def apply(light, edits, noise, scale=1.):
    if any(edits['wavelets']):
        if abs(scale-1) < 1e-6:
            # the app's own units: layer gain 1 + amount (lunar_finish takes PixInsight biases instead)
            light = wavelet_sharpen(light, edits['wavelets'], noise,
                                   [edits['wavelet_denoise']]*4, edits['wavelet_kernel'],
                                   gains=[1+b for b in edits['wavelets']])
        else:
            # Fit/zoom-out previews approximate native filter supports. Native exports
            # filter before resampling; users inspect actual detail at 1:1.
            c = light.astype(np.float32)
            extra = np.zeros_like(c)
            base = KERNELS[edits['wavelet_kernel']]
            for level, bias in enumerate(edits['wavelets']):
                positions = (np.arange(len(base))-(len(base)-1)/2)*(2**level)*scale
                radius = max(1, int(np.ceil(np.abs(positions).max())))
                kernel = np.zeros(2*radius+1, np.float32)
                for pos, weight in zip(positions, base):
                    lo = int(np.floor(pos)); frac = pos-lo
                    kernel[lo+radius] += weight*(1-frac)
                    if frac:
                        kernel[lo+radius+1] += weight*frac
                smooth = cv2.sepFilter2D(c, -1, kernel, kernel, borderType=cv2.BORDER_REFLECT)
                detail = c-smooth
                threshold = edits['wavelet_denoise']*noise[level]
                core = np.sign(detail)*np.maximum(np.abs(detail)-threshold, 0)
                extra += bias*core
                c = smooth
            light = light+extra
    if edits['usm_amount'] > 0:
        blurred = cv2.GaussianBlur(light, (0, 0), max(.05, edits['usm_radius']*scale))
        detail = light-blurred
        detail = np.where(np.abs(detail) >= edits['usm_threshold'], detail, 0)
        light = light+edits['usm_amount']*detail
    return np.clip(light, 0, 1)


def rgb_from_light(light):
    lab = np.zeros((*light.shape, 3), np.float32)
    lab[..., 0] = light*100
    return cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)


def fine_variation(before, after, kernel):
    layers, _ = atrous(before, 4, kernel)
    final, _ = atrous(after, 4, kernel)
    shape = (max(1, before.shape[1]//32), max(1, before.shape[0]//32))
    activity = cv2.resize(np.abs(layers[2]), shape, interpolation=cv2.INTER_AREA)
    lit = cv2.resize(before, shape, interpolation=cv2.INTER_AREA) > .05
    if not lit.any():
        return None
    quiet = lit & (activity <= np.percentile(activity[lit], 10))
    on = cv2.resize(quiet.astype(np.uint8), before.shape[::-1], interpolation=cv2.INTER_NEAREST) > 0
    original = float(np.mean(np.abs(layers[0][on])))
    return float(np.mean(np.abs(final[0][on]))/original) if original > 1e-7 else None
