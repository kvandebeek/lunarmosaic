"""Independent local checks on the already-warped mosaics; never alter the fitted transform."""
import cv2
import numpy as np


def measure(scene, progress=lambda f: None, cancelled=lambda: False):
    if scene.match is None:
        raise ValueError('Match the colour layer before checking alignment.')
    scale = min(1.0, 2200/max(scene.l.w, scene.l.h))
    matrix = np.diag([scale, scale, 1.])
    size = (max(1, round(scene.l.w*scale)), max(1, round(scene.l.h*scale)))
    l, lm = scene.layer('L', matrix, size)
    rgb, cm = scene.layer('RGB', matrix @ scene.match, size)
    a, b = [cv2.cvtColor(im, cv2.COLOR_RGB2GRAY) for im in (l, rgb)]
    # Both cameras have different sampling/seeing. Compare common lower-frequency detail.
    a, b = [cv2.GaussianBlur(im, (0, 0), .7) for im in (a, b)]
    width, height = size
    patch = min(256, max(64, min(size)//5))
    if min(size) < patch:
        return dict(method='local ECC translation', proxy_scale=scale, samples=[], measured=0,
                    unknown=0, median=None, p95=None, worst=None, message='Image is too small for local checks.')
    xs = np.linspace(patch/2, width-patch/2, 6)
    ys = np.linspace(patch/2, height-patch/2, 6)
    samples = []
    for cy in ys:
        for cx in xs:
            if cancelled():
                raise InterruptedError('Alignment check cancelled; the previous result was kept.')
            x, y = int(round(cx-patch/2)), int(round(cy-patch/2))
            aa, bb = a[y:y+patch, x:x+patch], b[y:y+patch, x:x+patch]
            sample = dict(x=float(cx/scale), y=float(cy/scale),
                          box=[float(v/scale) for v in (x, y, patch, patch)], state='unknown')
            valid = (lm[y:y+patch, x:x+patch] > .995) & (cm[y:y+patch, x:x+patch] > .995)
            if valid.mean() < .98:
                sample['reason'] = 'Insufficient shared coverage'
            elif min(float(aa.std()), float(bb.std())) < .004:
                sample['reason'] = 'Too little detail'
            else:
                try:
                    aa, bb = [np.ascontiguousarray((im-im.mean())/max(float(im.std()), 1e-6), dtype=np.float32) for im in (aa, bb)]
                    window = cv2.createHanningWindow((patch, patch), cv2.CV_32F)
                    # phaseCorrelate modifies its arguments when applying the window.
                    shift, response = cv2.phaseCorrelate(aa.copy(), bb.copy(), window)
                    if response < .25 or max(abs(v) for v in shift) > patch*.15:
                        raise ValueError('Ambiguous correlation or offset outside the local search')
                    warp = np.array([[1, 0, shift[0]], [0, 1, shift[1]]], np.float32)
                    correlation, warp = cv2.findTransformECC(aa, bb, warp, cv2.MOTION_TRANSLATION,
                        (cv2.TERM_CRITERIA_EPS|cv2.TERM_CRITERIA_COUNT, 120, 1e-5), None, 5)
                    dx, dy = (float(v)/scale for v in warp[:, 2])
                    if correlation < .7 or not np.isfinite([dx, dy]).all() or max(abs(dx), abs(dy))*scale > patch*.15:
                        raise ValueError('Local match was not reliable enough')
                    sample.update(state='measured', dx=dx, dy=dy, residual=float(np.hypot(dx, dy)),
                                  correlation=float(correlation))
                except (ValueError, cv2.error) as e:
                    sample['reason'] = str(e).split('\n')[0][:160]
            samples.append(sample)
            progress(len(samples)/36)
    residuals = [s['residual'] for s in samples if s['state'] == 'measured']
    return dict(method='local ECC translation', proxy_scale=scale, samples=samples,
                measured=len(residuals), unknown=len(samples)-len(residuals),
                median=float(np.median(residuals)) if residuals else None,
                p95=float(np.percentile(residuals, 95)) if residuals else None,
                worst=float(max(residuals)) if residuals else None,
                message='Offsets measured independently in sampled overlapping patches. Unmeasured areas remain unknown.')
