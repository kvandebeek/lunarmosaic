"""Render preview-window design mockups (3 styles × 3 stages) from real data.

Reads the panels listed in a layout JSON written by mosaic_builder.py (stage 3) and renders
A (Mission Control), B (Observatory) and C (Blueprint) for stages 1, 2 and 3, plus a contact sheet each.

    python3 render_mockups.py --layout mosaic_layout.json [--out DIR]
"""
import argparse, json, os, sys
from concurrent.futures import ThreadPoolExecutor
import numpy as np, cv2
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import mosaic_common as mc

ap = argparse.ArgumentParser(description=__doc__, formatter_class=mc.HelpFormatter)
ap.add_argument("--layout", default="mosaic_layout.json", help="layout JSON written by mosaic_builder.py")
ap.add_argument("--out", default=HERE, help="output folder")
ap.add_argument("--stage1-panel", default="2103_9", help="panel id shown in the feature mockup")
ap.add_argument("--stage2-pair", default="2117_0,2117_6", help="two panel ids shown in the matching mockup")
ap.add_argument("--view", choices=("natural", "machine"), default="natural",
                help="natural: gentle global tone curve on linear luminance (what you would see); "
                     "machine: the contrast-enhanced proxy the feature detector works on")
args = ap.parse_args()
LAYOUT, OUT = args.layout, args.out
os.makedirs(OUT, exist_ok=True)
W, H = 1600, 900
F = {"sf": "/System/Library/Fonts/SFNS.ttf", "mono": "/System/Library/Fonts/SFNSMono.ttf",
     "avenir": "/System/Library/Fonts/Avenir Next.ttc", "menlo": "/System/Library/Fonts/Menlo.ttc"}
STAGES = ["Preflight", "Features", "Pair discovery", "Global alignment", "Fine alignment",
          "Photometry", "Region selection", "Blending", "Output"]

lay = json.load(open(LAYOUT))
panels = lay["panels"]
ids = sorted(panels)


def _load(n):
    """Proxy (as the pipeline sees it), its feature mask, and 1/4× luminance for composites."""
    x, _, _ = mc.read_image(panels[n]["path"])
    L = mc.luminance(x)
    _, m, _, _, _, _ = mc.subject_and_sky(L, x, border=32, erode=8)
    P8, pm = mc.make_proxy(L, m, 4)
    return n, P8, pm, cv2.resize(L, (L.shape[1] // 4, L.shape[0] // 4), interpolation=cv2.INTER_AREA)


print(f"reading {len(ids)} panels …", flush=True)
with ThreadPoolExecutor(8) as ex:
    DATA = {n: (P8, pm, lum) for n, P8, pm, lum in ex.map(_load, ids)}
offset = np.array(lay["canvas"]["offset"])
canvas = (lay["canvas"]["width"], lay["canvas"]["height"])


# ---------------------------------------------------------------- text + drawing helpers

def font(name, size, index=0):
    return ImageFont.truetype(F[name], size, index=index)


def text(img, xy, s, fnt, fill, anchor="la", alpha=255):
    """Draw anti-aliased text with PIL onto a BGR uint8 image (in place)."""
    pil = Image.fromarray(img[..., ::-1])
    layer = Image.new("RGBA", pil.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).text(xy, s, font=fnt, fill=(fill[2], fill[1], fill[0], alpha), anchor=anchor)
    pil = Image.alpha_composite(pil.convert("RGBA"), layer).convert("RGB")
    img[:] = np.asarray(pil)[..., ::-1]


def glow(base, layer, sigma=6, strength=1.6):
    """Additive glow: layer (BGR on black) blurred and added, plus the crisp layer on top."""
    g = cv2.GaussianBlur(layer.astype(np.float32), (0, 0), sigma) * strength
    out = base.astype(np.float32) + g + layer.astype(np.float32) * 0.9
    return np.clip(out, 0, 255).astype(np.uint8)


def fit(img, w, h):
    s = min(w / img.shape[1], h / img.shape[0])
    return cv2.resize(img, (int(img.shape[1] * s), int(img.shape[0] * s)), interpolation=cv2.INTER_AREA), s


def paste(dst, src, x, y):
    dst[y:y + src.shape[0], x:x + src.shape[1]] = src


def vignette(img, strength=0.55):
    h, w = img.shape[:2]
    Y, X = np.ogrid[:h, :w]
    r = np.hypot((X - w / 2) / (w / 2), (Y - h / 2) / (h / 2))
    m = np.clip(1 - strength * np.clip(r - 0.35, 0, None) ** 1.5, 0, 1)
    return (img * m[..., None]).astype(np.uint8)


def tint(gray, color_lo, color_hi):
    g = gray.astype(np.float32)[..., None] / 255
    return np.clip(np.array(color_lo) * (1 - g) + np.array(color_hi) * g, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------- real data

def natural(i):
    """Linear luminance with one global tone curve: white point at the 99.8th percentile of the disk, mild gamma."""
    L = DATA[i][2]
    disk = L > 0.01
    wp = np.percentile(L[disk], 99.8) if disk.any() else 1.0
    return (np.clip(L / wp, 0, 1) ** (1 / 1.25) * 255).astype(np.uint8)


def proxy(i):
    """The image shown to the viewer (natural or machine view) and the feature mask."""
    return (natural(i) if args.view == "natural" else DATA[i][0]), DATA[i][1]


def machine(i):
    return DATA[i][0]


def sift_kp(P, pm):
    s = cv2.SIFT_create(nfeatures=4000, contrastThreshold=0.02)
    kp, d = s.detectAndCompute(P, pm)
    return kp, d


def composite_parts(scale):
    """Per-panel warped luminance at `scale` of the canvas, for placement animations."""
    Wc, Hc = int(canvas[0] * scale) + 2, int(canvas[1] * scale) + 2
    parts = {}
    for n in ids:
        w, h = panels[n]["size"]
        a, b, tx, ty = panels[n]["pose_abtxty"]
        L = DATA[n][2]
        f = 4
        Cp = np.array([[f, 0, (f - 1) / 2 - (w - 1) / 2], [0, f, (f - 1) / 2 - (h - 1) / 2], [0, 0, 1]])
        Pm = np.array([[a, -b, tx - offset[0]], [b, a, ty - offset[1]], [0, 0, 1]])
        M = (np.diag([scale, scale, 1]) @ Pm @ Cp)[:2]
        wm = np.zeros_like(L); wm[8:-8, 8:-8] = 1
        parts[n] = (cv2.warpAffine(L * wm, M, (Wc, Hc), flags=cv2.INTER_LINEAR),
                    cv2.warpAffine(wm, M, (Wc, Hc), flags=cv2.INTER_LINEAR))
    return parts, (Wc, Hc)


def quad(n, scale):
    w, h = panels[n]["size"]
    a, b, tx, ty = panels[n]["pose_abtxty"]
    c = np.array([[0, 0], [w, 0], [w, h], [0, h]], np.float64) - 0.5 - [(w - 1) / 2, (h - 1) / 2]
    q = np.stack([a * c[:, 0] - b * c[:, 1] + tx, b * c[:, 0] + a * c[:, 1] + ty], 1)
    return ((q - offset) * scale).astype(np.int32)


def placement_order():
    nb = {n: set() for n in ids}
    for p in lay["pairs"]:
        nb[p["a"]].add(p["b"]); nb[p["b"]].add(p["a"])
    order, seen, fr = [], {lay["reference"]}, [lay["reference"]]
    while fr:
        order += fr
        nxt = sorted({m for n in fr for m in nb[n]} - seen)
        seen |= set(nxt); fr = nxt
    return order


def composite(parts, names, size):
    acc = np.zeros(size[::-1], np.float32); ws = np.zeros(size[::-1], np.float32)
    for n in names:
        acc += parts[n][0]; ws += parts[n][1]
    c = acc / np.maximum(ws, 1e-6)
    return np.clip(c / 0.75 * 255, 0, 255).astype(np.uint8), ws > 0.01


# scene data -------------------------------------------------------
S1_ID = args.stage1_panel
P1, pm1 = proxy(S1_ID)
kp1, _ = sift_kp(machine(S1_ID), pm1)
S2 = tuple(args.stage2_pair.split(","))
Pa, pma = proxy(S2[0]); Pb, pmb = proxy(S2[1])
fa = mc.detect_features(machine(S2[0]), pma); fb = mc.detect_features(machine(S2[1]), pmb)
res2, _ = mc.verify_pair(fa, fb, pma, pmb)
order = placement_order()
K3 = 62                                  # placement frame: 62 of 101 panels placed
rms = {n: panels[n]["rms_px"] for n in ids}


def heat(e):
    """Residual colour: teal (good) → amber → coral (worse), BGR."""
    t = float(np.clip(e, 0, 1.2))
    stops = [(0.0, (190, 210, 30)), (0.35, (190, 210, 30)), (0.6, (40, 200, 255)), (0.9, (40, 170, 255)), (1.2, (90, 90, 250))]
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        if t <= t1:
            u = (t - t0) / (t1 - t0)
            return tuple(int(c0[k] + (c1[k] - c0[k]) * u) for k in range(3))
    return stops[-1][1]


# ================================================================ STYLE A · Mission Control

A_BG, A_PANEL, A_LINE, A_TXT, A_DIM, A_ACC, A_OK = (22, 19, 17), (34, 30, 27), (58, 52, 48), (236, 236, 236), (140, 136, 132), (255, 200, 60), (120, 210, 90)
SIDE = 420


def a_sidebar(img, stage, progress, stats, mini=None, spark=None):
    x0 = W - SIDE
    cv2.rectangle(img, (x0, 0), (W, H), A_PANEL, -1)
    cv2.line(img, (x0, 0), (x0, H), A_LINE, 1)
    text(img, (x0 + 28, 30), "MOSAIC BUILDER", font("sf", 15), A_DIM)
    text(img, (x0 + 28, 52), "2026-09-25 · Moon · 101 panels", font("sf", 19), A_TXT)
    y = 104
    for k, name in enumerate(STAGES):
        done, cur = k < stage, k == stage
        col = A_OK if done else A_ACC if cur else A_LINE
        cx = x0 + 38
        if done:
            cv2.circle(img, (cx, y), 7, col, -1, cv2.LINE_AA)
        elif cur:
            cv2.circle(img, (cx, y), 11, tuple(int(c * 0.35) for c in col), -1, cv2.LINE_AA)
            cv2.circle(img, (cx, y), 7, col, -1, cv2.LINE_AA)
        else:
            cv2.circle(img, (cx, y), 7, col, 1, cv2.LINE_AA)
        if k < len(STAGES) - 1:
            cv2.line(img, (cx, y + 10), (cx, y + 24), A_LINE, 1, cv2.LINE_AA)
        text(img, (cx + 22, y), f"{k}  {name}", font("sf", 17 if cur else 16), A_TXT if (cur or done) else A_DIM, anchor="lm")
        if cur:
            bx, bw = cx + 190, SIDE - 250
            cv2.rectangle(img, (bx, y - 3), (bx + bw, y + 3), A_LINE, -1)
            cv2.rectangle(img, (bx, y - 3), (bx + int(bw * progress), y + 3), A_ACC, -1)
        y += 34
    y += 6
    cw, ch, gap = (SIDE - 56 - 12) // 2, 70, 12
    for k, (label, value) in enumerate(stats):
        cx_, cy_ = x0 + 28 + (k % 2) * (cw + gap), y + (k // 2) * (ch + gap)
        cv2.rectangle(img, (cx_, cy_), (cx_ + cw, cy_ + ch), (44, 40, 36), -1)
        text(img, (cx_ + 14, cy_ + 12), label.upper(), font("sf", 12), A_DIM)
        text(img, (cx_ + 14, cy_ + 32), value, font("sf", 24), A_TXT)
    y += ((len(stats) + 1) // 2) * (ch + gap) + 8
    if spark is not None:
        text(img, (x0 + 28, y), spark[0].upper(), font("sf", 12), A_DIM)
        vals = np.array(spark[1], float)
        gx, gy, gw, gh = x0 + 28, y + 20, SIDE - 56, 60
        v = (vals - vals.min()) / max(1e-9, np.ptp(vals))
        pts = np.stack([gx + np.linspace(0, gw, len(v)), gy + gh - v * gh], 1).astype(np.int32)
        fill = np.vstack([pts, [[gx + gw, gy + gh], [gx, gy + gh]]])
        ov = img.copy(); cv2.fillPoly(ov, [fill], (70, 60, 30)); img[:] = cv2.addWeighted(ov, 0.6, img, 0.4, 0)
        cv2.polylines(img, [pts], False, A_ACC, 2, cv2.LINE_AA)
        y += 100
    if mini is not None:
        m, _ = fit(mini, SIDE - 56, H - y - 24)
        paste(img, m, x0 + 28 + (SIDE - 56 - m.shape[1]) // 2, y)


def a_header(img, title, sub):
    text(img, (32, 26), title, font("sf", 26), A_TXT)
    text(img, (32, 62), sub, font("sf", 16), A_DIM)


def style_a(stage):
    img = np.full((H, W, 3), A_BG, np.uint8)
    vw, vh, vx, vy = W - SIDE - 64, H - 130, 32, 100
    if stage == 1:
        v = cv2.cvtColor(P1, cv2.COLOR_GRAY2BGR)
        v = (v * 0.8).astype(np.uint8)
        for k in kp1[::2]:
            cv2.circle(v, (int(k.pt[0]), int(k.pt[1])), max(2, int(k.size * 0.6)), A_ACC, 1, cv2.LINE_AA)
        v, _ = fit(v, vw, vh)
        paste(img, v, vx, vy)
        a_header(img, "Finding features", f"Panel {S1_ID}  ·  {len(kp1)} keypoints on the 1/4× proxy (contrast-enhanced)")
        a_sidebar(img, 1, 0.57, [("Panel", "58 / 101"), ("Keypoints", f"{len(kp1)}"), ("Median / panel", "3,983"), ("Elapsed", "00:04")],
                  spark=("Keypoints per panel", np.random.default_rng(3).normal(3900, 300, 58).clip(800, 4000)))
    elif stage == 2:
        A = cv2.cvtColor(Pa, cv2.COLOR_GRAY2BGR); B = cv2.cvtColor(Pb, cv2.COLOR_GRAY2BGR)
        gap = 24
        both = np.full((max(A.shape[0], B.shape[0]), A.shape[1] + B.shape[1] + gap, 3), A_BG, np.uint8)
        paste(both, (A * 0.8).astype(np.uint8), 0, 0); paste(both, (B * 0.8).astype(np.uint8), A.shape[1] + gap, 0)
        s_, _ = fit(both, vw, vh)
        sc = s_.shape[1] / both.shape[1]
        layer = np.zeros_like(s_)
        for (x1, y1), (x2, y2) in list(zip(res2["pts_a"], res2["pts_b"]))[::5]:
            cv2.line(layer, (int(x1 * sc), int(y1 * sc)), (int((x2 + A.shape[1] + gap) * sc), int(y2 * sc)), (60, 170, 110), 1, cv2.LINE_AA)
        s_ = cv2.addWeighted(s_, 1, layer, 0.8, 0)
        paste(img, s_, vx, vy + (vh - s_.shape[0]) // 2)
        a_header(img, "Matching overlaps", f"{S2[0]}  ↔  {S2[1]}  ·  {res2['inliers']} inliers  ·  rotation {res2['rot_deg']:+.2f}°  ·  scale {res2['scale']:.4f}")
        a_sidebar(img, 2, 0.54, [("Candidates", "1,140"), ("Verified", "612"), ("Accepted", "371"), ("Rejected", "241")],
                  spark=("Accepted pairs", np.cumsum(np.random.default_rng(1).random(60) > 0.4)))
    else:
        comp, cov = composite(PARTS, order[:K3], PSIZE)
        v = cv2.cvtColor(comp, cv2.COLOR_GRAY2BGR)
        v = np.where(cov[..., None], v, np.array(A_BG, np.uint8))
        for n in order[:K3]:
            q = quad(n, PSCALE)
            cv2.polylines(v, [q], True, heat(rms[n]), 1, cv2.LINE_AA)
        cur = order[K3 - 1]
        cv2.polylines(v, [quad(cur, PSCALE)], True, A_ACC, 3, cv2.LINE_AA)
        v, _ = fit(v, vw, vh)
        paste(img, v, vx + (vw - v.shape[1]) // 2, vy)
        a_header(img, "Placing panels", f"Global similarity solve  ·  {K3} of 101 placed  ·  newest {cur} (residual {rms[cur]:.2f} px)")
        mini_c, _ = composite(PARTS, ids, PSIZE)
        a_sidebar(img, 3, K3 / 101, [("Placed", f"{K3} / 101"), ("Layout RMS", "0.62 px"), ("Pairs used", "622"), ("Dropped", "59")],
                  spark=("Layout RMS per solve iteration", [0.73, 0.64, 0.62, 0.62, 0.62]))
    return img


# ================================================================ STYLE B · Observatory

B_AMBER, B_WHITE, B_DIM = (70, 180, 255), (245, 245, 245), (150, 150, 150)


def b_chrome(img, stage, title, detail, progress, readouts):
    ov = img.copy()
    cv2.rectangle(ov, (0, 0), (W, 96), (0, 0, 0), -1)
    cv2.rectangle(ov, (0, H - 96), (W, H), (0, 0, 0), -1)
    img[:] = cv2.addWeighted(ov, 0.55, img, 0.45, 0)
    text(img, (40, 26), f"STAGE {stage} OF 8", font("avenir", 14, 5), B_AMBER)
    text(img, (40, 46), title, font("avenir", 30, 2), B_WHITE)
    text(img, (W - 40, 30), "Moon · 2026-09-25", font("avenir", 16, 2), B_DIM, anchor="ra")
    text(img, (W - 40, 54), detail, font("avenir", 16, 2), B_WHITE, anchor="ra")
    py = H - 76
    cv2.line(img, (40, py), (W - 40, py), (70, 70, 70), 1, cv2.LINE_AA)
    cv2.line(img, (40, py), (40 + int((W - 80) * progress), py), B_AMBER, 2, cv2.LINE_AA)
    cv2.circle(img, (40 + int((W - 80) * progress), py), 4, B_AMBER, -1, cv2.LINE_AA)
    x = 40
    for label, value in readouts:
        text(img, (x, H - 58), label.upper(), font("avenir", 12, 5), B_DIM)
        text(img, (x, H - 38), value, font("mono", 17), B_WHITE)
        x += 220
    text(img, (W - 40, H - 38), f"{progress * 100:.0f} %", font("mono", 17), B_AMBER, anchor="ra")


def style_b(stage):
    img = np.zeros((H, W, 3), np.uint8)
    if stage == 1:
        v = cv2.cvtColor(P1, cv2.COLOR_GRAY2BGR)
        v, s = fit(v, W - 80, H - 210)
        v = (v * (0.92 if args.view == "natural" else 0.75)).astype(np.uint8)
        paste(img, v, (W - v.shape[1]) // 2, (H - v.shape[0]) // 2)
        layer = np.zeros_like(img)
        ox, oy = (W - v.shape[1]) // 2, (H - v.shape[0]) // 2
        for k in kp1[::2]:
            r = int(np.clip(k.size * s * 0.12, 1, 4))
            cv2.circle(layer, (int(k.pt[0] * s) + ox, int(k.pt[1] * s) + oy), r, (60, 150, 230), -1, cv2.LINE_AA)
        img = glow(img, layer, sigma=3, strength=1.2)
        img = vignette(img)
        b_chrome(img, 1, "Finding features", f"panel {S1_ID} · {len(kp1)} keypoints", 0.57,
                 [("panel", "58 / 101"), ("keypoints", f"{len(kp1)}"), ("elapsed", "00:04")])
    elif stage == 2:
        A = cv2.cvtColor(Pa, cv2.COLOR_GRAY2BGR); B = cv2.cvtColor(Pb, cv2.COLOR_GRAY2BGR)
        both = np.zeros((max(A.shape[0], B.shape[0]), A.shape[1] + B.shape[1] + 8, 3), np.uint8)
        paste(both, A, 0, 0); paste(both, B, A.shape[1] + 8, 0)
        v, s = fit(both, W - 80, H - 210)
        v = (v * (0.9 if args.view == "natural" else 0.7)).astype(np.uint8)
        ox, oy = (W - v.shape[1]) // 2, (H - v.shape[0]) // 2 + 10
        paste(img, v, ox, oy)
        layer = np.zeros_like(img)
        for (x1, y1), (x2, y2) in list(zip(res2["pts_a"], res2["pts_b"]))[::4]:
            p1 = (int(x1 * s) + ox, int(y1 * s) + oy); p2 = (int((x2 + A.shape[1] + 8) * s) + ox, int(y2 * s) + oy)
            cv2.line(layer, p1, p2, (90, 90, 90), 1, cv2.LINE_AA)
            cv2.circle(layer, p1, 2, B_AMBER, -1, cv2.LINE_AA); cv2.circle(layer, p2, 2, B_AMBER, -1, cv2.LINE_AA)
        img = glow(img, layer, sigma=2, strength=0.8)
        img = vignette(img, 0.4)
        b_chrome(img, 2, "Matching overlaps", f"{S2[0]} – {S2[1]} · {res2['inliers']} inliers", 0.54,
                 [("candidates", "1,140"), ("accepted", "371"), ("rotation", f"{res2['rot_deg']:+.2f}°"), ("scale", f"{res2['scale']:.4f}")])
    else:
        comp, cov = composite(PARTS, order[:K3], PSIZE)
        v = cv2.cvtColor(comp, cv2.COLOR_GRAY2BGR)
        layer = np.zeros_like(v)
        for n in order[:K3 - 1]:
            cv2.polylines(layer, [quad(n, PSCALE)], True, (46, 46, 46), 1, cv2.LINE_AA)
        cur = order[K3 - 1]
        cv2.polylines(layer, [quad(cur, PSCALE)], True, B_AMBER, 2, cv2.LINE_AA)
        v = glow(v, layer, sigma=4, strength=0.7)
        v, s = fit(v, W - 80, H - 210)
        paste(img, v, (W - v.shape[1]) // 2, (H - v.shape[0]) // 2 + 10)
        img = vignette(img, 0.35)
        b_chrome(img, 3, "Placing panels", f"{K3} of 101 · newest {cur}", K3 / 101,
                 [("placed", f"{K3} / 101"), ("layout rms", "0.62 px"), ("pairs", "622"), ("rotation", "−0.07…+0.33°")])
    return img


# ================================================================ STYLE C · Blueprint

C_BG, C_GRID, C_CYAN, C_MAG, C_TXT, C_DIM = (48, 24, 10), (80, 48, 24), (255, 220, 80), (200, 90, 255), (255, 240, 210), (190, 150, 110)


def c_base():
    img = np.full((H, W, 3), C_BG, np.uint8)
    for x in range(0, W, 40):
        cv2.line(img, (x, 0), (x, H), C_GRID if x % 200 else (100, 62, 32), 1)
    for y in range(0, H, 40):
        cv2.line(img, (0, y), (W, y), C_GRID if y % 200 else (100, 62, 32), 1)
    return img


def brackets(img, x0, y0, x1, y1, col, L=26, t=2):
    for (x, y, dx, dy) in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
        cv2.line(img, (x, y), (x + dx * L, y), col, t, cv2.LINE_AA)
        cv2.line(img, (x, y), (x, y + dy * L), col, t, cv2.LINE_AA)


def c_hud(img, stage, title, lines, progress):
    text(img, (34, 28), f"// STAGE_{stage:02d}", font("menlo", 15), C_MAG)
    text(img, (34, 50), title.upper().replace(" ", "_"), font("menlo", 28), C_CYAN)
    y = 110
    for k, v in lines:
        text(img, (W - 380, y), f"{k:<14}", font("menlo", 14), C_DIM)
        text(img, (W - 190, y), v, font("menlo", 14), C_TXT)
        y += 24
    bx, by, bw = W - 380, y + 12, 340
    n = 34
    for i in range(n):
        col = C_CYAN if i / n < progress else C_GRID
        cv2.rectangle(img, (bx + i * (bw // n), by), (bx + i * (bw // n) + bw // n - 3, by + 10), col, -1)
    text(img, (bx, by + 22), f"PROGRESS {progress * 100:5.1f}%", font("menlo", 13), C_DIM)
    text(img, (34, H - 34), "MOSAIC_BUILDER v0.3  ·  MOON 2026-09-25  ·  101 PANELS", font("menlo", 13), C_DIM)


def style_c(stage):
    img = c_base()
    area = (34, 90, W - 440, H - 70)
    aw, ah = area[2] - area[0], area[3] - area[1]
    if stage == 1:
        g, s = fit(P1, aw, ah)
        v = tint(g, C_BG, (255, 235, 190))
        ox, oy = area[0] + (aw - v.shape[1]) // 2, area[1] + (ah - v.shape[0]) // 2
        paste(img, v, ox, oy)
        layer = np.zeros_like(img)
        for k in kp1[::2]:
            x, y = int(k.pt[0] * s) + ox, int(k.pt[1] * s) + oy
            cv2.line(layer, (x - 3, y), (x + 3, y), C_MAG, 1); cv2.line(layer, (x, y - 3), (x, y + 3), C_MAG, 1)
        img = glow(img, layer, sigma=2.5, strength=1.2)
        brackets(img, ox - 8, oy - 8, ox + v.shape[1] + 8, oy + v.shape[0] + 8, C_CYAN)
        c_hud(img, 1, "Feature extraction", [("PANEL", f"{S1_ID}"), ("INDEX", "58/101"), ("KEYPOINTS", f"{len(kp1)}"),
                                             ("DETECTOR", "SIFT c=0.02"), ("PROXY", "0.25x CLAHE")], 0.57)
    elif stage == 2:
        A = tint(Pa, C_BG, (255, 235, 190)); B = tint(Pb, C_BG, (255, 235, 190))
        both = np.full((max(A.shape[0], B.shape[0]), A.shape[1] + B.shape[1] + 30, 3), C_BG, np.uint8)
        paste(both, A, 0, 0); paste(both, B, A.shape[1] + 30, 0)
        v, s = fit(both, aw, ah)
        ox, oy = area[0] + (aw - v.shape[1]) // 2, area[1] + (ah - v.shape[0]) // 2
        paste(img, v, ox, oy)
        layer = np.zeros_like(img)
        for (x1, y1), (x2, y2) in list(zip(res2["pts_a"], res2["pts_b"]))[::5]:
            p1 = (int(x1 * s) + ox, int(y1 * s) + oy); p2 = (int((x2 + A.shape[1] + 30) * s) + ox, int(y2 * s) + oy)
            cv2.line(layer, p1, p2, (160, 120, 40), 1, cv2.LINE_AA)
            cv2.circle(layer, p1, 2, C_MAG, -1, cv2.LINE_AA); cv2.circle(layer, p2, 2, C_MAG, -1, cv2.LINE_AA)
        img = glow(img, layer, sigma=3, strength=1.0)
        brackets(img, ox - 8, oy - 8, ox + v.shape[1] + 8, oy + v.shape[0] + 8, C_CYAN)
        c_hud(img, 2, "Pair discovery", [("PAIR", f"{S2[0]}<>{S2[1]}"), ("INLIERS", f"{res2['inliers']}"), ("ROTATION", f"{res2['rot_deg']:+.3f} deg"),
                                         ("SCALE", f"{res2['scale']:.5f}"), ("CANDIDATES", "1140"), ("ACCEPTED", "371")], 0.54)
    else:
        comp, cov = composite(PARTS, order[:K3], PSIZE)
        v = tint(comp, C_BG, (255, 235, 190))
        v = np.where(cov[..., None], v, img[:1, :1].copy() * 0 + np.array(C_BG, np.uint8))
        layer = np.zeros_like(v)
        for n in order[:K3]:
            cv2.polylines(layer, [quad(n, PSCALE)], True, tuple(int(c * 0.55) for c in heat(rms[n])), 1, cv2.LINE_AA)
        cur = order[K3 - 1]
        cv2.polylines(layer, [quad(cur, PSCALE)], True, C_MAG, 2, cv2.LINE_AA)
        v = glow(v, layer, sigma=2.5, strength=0.7)
        v, s = fit(v, aw, ah)
        ox, oy = area[0] + (aw - v.shape[1]) // 2, area[1] + (ah - v.shape[0]) // 2
        paste(img, v, ox, oy)
        brackets(img, ox - 8, oy - 8, ox + v.shape[1] + 8, oy + v.shape[0] + 8, C_CYAN)
        c_hud(img, 3, "Global alignment", [("PLACED", f"{K3}/101"), ("NEWEST", cur), ("RESIDUAL", f"{rms[cur]:.2f} px"),
                                           ("LAYOUT_RMS", "0.62 px"), ("PAIRS", "622"), ("DROPPED", "59"), ("MODEL", "similarity 4dof")], K3 / 101)
        # convergence graph
        gx, gy, gw, gh = W - 380, 470, 340, 120
        cv2.rectangle(img, (gx, gy), (gx + gw, gy + gh), C_GRID, 1)
        vals = np.array([0.73, 0.64, 0.62, 0.62, 0.62])
        pts = np.stack([gx + np.linspace(10, gw - 10, len(vals)), gy + gh - 10 - (vals - 0.55) / 0.2 * (gh - 20)], 1).astype(np.int32)
        layer = np.zeros_like(img)
        cv2.polylines(layer, [pts], False, C_CYAN, 2, cv2.LINE_AA)
        for p in pts:
            cv2.circle(layer, tuple(p), 4, C_MAG, -1, cv2.LINE_AA)
        img = glow(img, layer, sigma=3, strength=1.0)
        text(img, (gx, gy - 20), "RMS_CONVERGENCE [px]", font("menlo", 13), C_DIM)
    return img


PSCALE = min((W - SIDE - 64) / canvas[0], (H - 130) / canvas[1])
PARTS, PSIZE = composite_parts(PSCALE)
suffix = "" if args.view == "machine" else "_natural"
for name, fn in (("A_mission_control", style_a), ("B_observatory", style_b), ("C_blueprint", style_c)):
    frames = []
    for st in (1, 2, 3):
        im = fn(st)
        cv2.imwrite(f"{OUT}/{name}{suffix}_stage{st}.png", im)
        frames.append(im)
    sheet = np.vstack([np.hstack([cv2.resize(frames[0], (W // 2, H // 2)), cv2.resize(frames[1], (W // 2, H // 2))]),
                       np.hstack([cv2.resize(frames[2], (W // 2, H // 2)), np.full((H // 2, W // 2, 3), 12, np.uint8)])])
    cv2.imwrite(f"{OUT}/{name}{suffix}_sheet.png", sheet)
    print("wrote", name)
