"""Run builder stages 0-3 in-process and capture the fine-alignment inputs (no files published)."""
import os, sys, os, types
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
import numpy as np, cv2
import mosaic_common as mc, mosaic_stages as ms, mosaic_builder as mb, preview_style as ps

class Captured(Exception):
    pass

def capture(panels=os.environ.get('MOSAIC_PANELS', 'panels'), out_dir=os.path.dirname(os.path.abspath(__file__))):
    args = types.SimpleNamespace(inputs=[panels], output=os.path.join(out_dir, 'cap.tif'), manifest=None, exclude_tier='',
        proxy_factor=4, features=4000, min_keypoints=150, pair_search='index', max_points=300, stop_after=8, model='poly3',
        levels=7, border='auto', background='neutral-black', limb_feather=12, colour_balance='highlands',
        workers=9, no_preview=True, display_tif=False, record=None, record_vertical=None, log=None, log_pairs=False)
    pr = mc.Progress(mb.N_STAGES, None)
    pv = mb.Preview(False, ps.Observatory())
    stg = mc.Staging()
    box = {}
    real = ms.fine_align
    def grab(*a, **k):
        names = ['nodes', 'pair_keys', 'sizes', 'L', 'M', 'tex4', 'f', 'theta_sim', 'idx_sim', 'ref', 'model', 'coarse', 'pool']
        box.update(dict(zip(names, a)))
        raise Captured()
    ms.fine_align = grab
    try:
        mb.pipeline(args, pr, pv, stg)
    except Captured:
        pass
    finally:
        ms.fine_align = real
        stg.discard()
    return types.SimpleNamespace(**box)
