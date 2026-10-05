"""What the chain editor shows for each block (plan phase 7, Appendix A): its name, group, the units the owner sees
in PixInsight and Photoshop, and which paths it may sit on — derived from blocks.BLOCKS, so the editor and the
engine cannot disagree about a parameter's range or default.

    catalog() -> [{type, label, group, paths, detail, note, params: [{name, label, kind, default, min, max, step,
                   unit, choices, …}]}]
"""
import blocks as B

GROUPS = ('tone', 'detail', 'colour')
# type: (label, group, one line on what it matches, per-parameter (label, unit, step))
UI = {
    'levels': ('Levels', 'tone', 'Photoshop Levels on the stored values', {
        'black': ('Black', 'levels', 1), 'white': ('White', 'levels', 1), 'gamma': ('Gamma', '', 0.01),
        'out_black': ('Output black', 'levels', 1), 'out_white': ('Output white', 'levels', 1)}),
    'curves': ('Curves', 'tone', 'Photoshop Curves: a smooth curve through your points', {
        'points': ('Points', 'levels', 1)}),
    'highlights': ('Highlights', 'tone', 'lunar_finish --highlights: an edge-aware shoulder', {
        'strength': ('Strength', '', 0.01), 'knee_percentile': ('Knee', 'percentile', 0.5)}),
    'contrast': ('Contrast', 'tone', 'lunar_finish --contrast: a gentle S-curve; below zero flattens', {
        'strength': ('Strength', '', 0.01)}),
    'wavelets': ('À-trous wavelets', 'detail', 'PixInsight ATrousWaveletTransform: gain 1 + 10 × bias', {
        'biases': ('Bias per layer', 'PixInsight bias', 0.05), 'kernel': ('Kernel', '', None),
        'denoise': ('Grain protection per layer', '× noise', 0.1)}),
    'unsharp_mask': ('Unsharp mask', 'detail', 'Photoshop Unsharp Mask: soft threshold, measured radius', {
        'amount': ('Amount', '%', 1), 'radius': ('Radius', 'px', 0.1), 'threshold': ('Threshold', 'levels', 1),
        'mode': ('Mode', '', None)}),
    'gaussian_blur': ('Gaussian blur', 'detail', 'on the colour path, colour noise reduction', {
        'sigma': ('Sigma', 'px', 0.1)}),
    'deconvolution': ('Deconvolution', 'detail', 'LuckyStackWorker\'s PSF, Wiener-preconditioned Landweber', {
        'psf': ('PSF', '', None), 'iterations': ('Iterations', '', 1), 'dering_strength': ('Dering', '', 0.05),
        'dering_radius': ('Dering radius', 'px', 0.5), 'blend_raw': ('Blend raw', '', 0.05)}),
    'white_balance': ('White balance', 'colour', 'PixInsight ColorCalibration: the sky is the fixed point', {
        'background': ('Background reference', '', None), 'white': ('White reference', '', None),
        'white_low': ('White limit, low', '', 0.01), 'white_high': ('White limit, high', '', 0.01),
        'structure_detection': ('Structure detection', '', None),
        'neutralize_background': ('Neutralise the background', '', None),
        'background_target': ('Background target', '', 0.005)}),
    'rgb_shift': ('Sub-pixel RGB shift', 'colour', 'red and blue onto green, measured twice and applied when both agree', {
        'mode': ('Mode', '', None), 'manual': ('Shift', 'px', 0.05)}),
    'saturation': ('Saturation', 'colour', 'lunar_finish\'s colour stage, weighted to lunar hues', {
        'factor': ('Factor', '×', 0.05), 'passes': ('Passes', '', 1), 'lunar_hue': ('Lunar-hue weighting', '', None),
        'chroma_blur_px': ('Colour smoothing', 'px', 0.5)}),
}
PATH_NAMES = {'luminance': 'luminance chain', 'colour': 'colour chain', 'final': 'final chain'}


def refusal(type, path):
    """Why a block cannot sit on a path, as the editor says it (None when it can)."""
    blk = B.BLOCKS[type]
    if path in blk.paths:
        return None
    where = ' and '.join(PATH_NAMES[p] for p in blk.paths)
    if type in ('saturation', 'white_balance', 'rgb_shift') and path == 'luminance':
        return f'{UI[type][0]} changes colour, which the luminance chain does not have. It works on the {where}.'
    if type in ('highlights', 'contrast') and path == 'colour':
        return f'{UI[type][0]} changes brightness, which the merge takes from the luminance. It works on the {where}.'
    return f'{UI[type][0]} works on the {where}.'


def param(name, spec, ui):
    label, unit, step = ui.get(name, (name.replace('_', ' ').capitalize(), '', None))
    out = dict(name=name, label=label, kind=spec.kind, default=spec.default, unit=unit, step=step)
    if spec.lo is not None:
        out['min'] = spec.lo
    if spec.hi is not None:
        out['max'] = spec.hi
    if spec.lo_open:
        out['min_open'] = True
    if spec.hi_open:
        out['max_open'] = True
    if spec.choices:
        out['choices'] = list(spec.choices)
    if spec.length:
        out['length'] = list(spec.length)
    return out


def catalog():
    out = []
    for type, (label, group, note, ui) in UI.items():
        blk = B.BLOCKS.get(type)
        if blk is None:
            continue
        out.append(dict(type=type, label=label, group=group, note=note, paths=list(blk.paths), detail=blk.detail,
                        refusals={p: refusal(type, p) for p in B.PATHS if refusal(type, p)},
                        params=[param(n, s, ui) for n, s in blk.params.items()]))
    return out
