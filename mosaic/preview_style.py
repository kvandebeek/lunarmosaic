"""Observatory style for the builder's preview window and movies (chosen 2026-09-27).

Full-bleed image on black, a natural tone rendering (the detector's contrast-enhanced proxy is never shown),
minimal translucent captions, glowing amber keypoints and thin linework.

Two formats:
  landscape 1920×1080 (window + --record)
  vertical  1080×1920 (--record-vertical, for Instagram Reels / Stories): text kept inside the safe zone, clear
            of the platform's top bar, bottom caption area and right-hand button column; pairs stacked vertically.
Text uses Pillow with macOS system fonts when available, otherwise OpenCV's Hershey font.
"""
import os
import re

import cv2
import numpy as np

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:          # optional: fall back to cv2 text
    Image = None

W, H = 1920, 1080            # landscape default (kept for callers that need the window size)
AMBER, WHITE, DIM, GREY = (70, 180, 255), (245, 245, 245), (150, 150, 150), (70, 70, 70)
RESID = {"good": (190, 210, 30), "mid": (40, 190, 255), "bad": (90, 90, 250)}      # teal / amber / coral (BGR)
FONTS = {"title": ("/System/Library/Fonts/Avenir Next.ttc", 2), "caps": ("/System/Library/Fonts/Avenir Next.ttc", 5),
         "mono": ("/System/Library/Fonts/SFNSMono.ttf", 0)}

_ASCII = {"·": "-", "↔": "<->", "×": "x", "–": "-", "—": "-", "°": " deg", "≥": ">=", "≤": "<=", "…": "...", "→": "->", "±": "+/-"}


def ascii_text(t):
    for k, v in _ASCII.items():
        t = t.replace(k, v)
    return t.encode("ascii", "replace").decode()


N_STEPS = 9          # pipeline stages 0–8, shown to viewers as steps 1–9


def step_label(stage, n_steps=N_STEPS, name="LUNAR MOSAIC"):
    return f"STEP {stage + 1} OF {n_steps}" if stage is not None else name


def residual_colour(e):
    return RESID["good"] if e < 0.5 else RESID["mid"] if e < 1.0 else RESID["bad"]


def tone(lum, mask=None, white_pct=99.8, gamma=1.1):
    """Natural rendering of linear luminance: one global white point and a mild gamma (no local contrast)."""
    sel = lum[mask] if mask is not None and mask.any() else lum[lum > 0.01]
    wp = float(np.percentile(sel, white_pct)) if sel.size else 1.0
    return (np.clip(lum / max(wp, 1e-6), 0, 1) ** (1 / gamma) * 255).astype(np.uint8)


class Observatory:
    # geometry per format: image area (top, bottom, side margin)
    GEOM = {"landscape": dict(top=150, bottom=130, margin=48),
            "vertical": dict(top=400, bottom=560, margin=40)}

    def __init__(self, session_label="", size=(1920, 1080)):
        self.W, self.H = size
        self.fmt = "vertical" if self.H > self.W else "landscape"
        g = self.GEOM[self.fmt]
        self.top, self.bottom, self.margin = g["top"], g["bottom"], g["margin"]
        self.area = (self.W - 2 * self.margin, self.H - self.top - self.bottom)
        self.session = session_label
        self.fonts = {}
        self.pil = Image is not None and all(os.path.exists(p) for p, _ in FONTS.values())
        self._texts = []
        self.log_line = ""
        self.n_steps = N_STEPS          # a second movie (finishing) uses its own number of steps
        self.name = "LUNAR MOSAIC"

    # ---------------------------------------------------------------- text (batched: one Pillow pass per frame)

    def _font(self, kind, size):
        key = (kind, size)
        if key not in self.fonts:
            path, idx = FONTS[kind]
            self.fonts[key] = ImageFont.truetype(path, size, index=idx)
        return self.fonts[key]

    def text(self, xy, s, kind, size, color, anchor="la"):
        self._texts.append((xy, s, kind, size, color, anchor))

    def _flush(self, img):
        if not self._texts:
            return img
        if self.pil:
            pil = Image.fromarray(img[..., ::-1].copy())
            d = ImageDraw.Draw(pil)
            for xy, s, kind, size, color, anchor in self._texts:
                d.text(xy, s, font=self._font(kind, size), fill=(color[2], color[1], color[0]), anchor=anchor)
            img = np.asarray(pil)[..., ::-1].copy()
        else:
            for (x, y), s, kind, size, color, anchor in self._texts:
                s = ascii_text(s)
                sc = size / 30
                (tw, th), _ = cv2.getTextSize(s, cv2.FONT_HERSHEY_SIMPLEX, sc, 1)
                x = x - tw if anchor[0] == "r" else x - tw // 2 if anchor[0] == "m" else x
                y = y + th if anchor[1] == "a" else y + th // 2 if anchor[1] == "m" else y
                cv2.putText(img, s, (int(x), int(y)), cv2.FONT_HERSHEY_SIMPLEX, sc, color, 1, cv2.LINE_AA)
        self._texts = []
        return img

    # ---------------------------------------------------------------- building blocks

    def blank(self):
        return np.zeros((self.H, self.W, 3), np.uint8)

    @staticmethod
    def glow(base, layer, sigma=4, strength=1.0):
        g = cv2.GaussianBlur(layer, (0, 0), sigma).astype(np.float32) * strength
        return np.clip(base.astype(np.float32) + g + layer.astype(np.float32) * 0.9, 0, 255).astype(np.uint8)

    @staticmethod
    def vignette(img, strength=0.4):
        h, w = img.shape[:2]
        Y, X = np.ogrid[:h, :w]
        r = np.hypot((X - w / 2) / (w / 2), (Y - h / 2) / (h / 2))
        m = np.clip(1 - strength * np.clip(r - 0.35, 0, None) ** 1.5, 0, 1).astype(np.float32)
        return (img * m[..., None]).astype(np.uint8)

    def place(self, img, content):
        """Centre content in the image area between the captions; returns (x0, y0, scale)."""
        aw, ah = self.area
        s = min(aw / content.shape[1], ah / content.shape[0])
        c = cv2.resize(content, (max(1, int(content.shape[1] * s)), max(1, int(content.shape[0] * s))),
                       interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
        if c.ndim == 2:
            c = cv2.cvtColor(c, cv2.COLOR_GRAY2BGR)
        x0, y0 = (self.W - c.shape[1]) // 2, self.top + (ah - c.shape[0]) // 2
        img[y0:y0 + c.shape[0], x0:x0 + c.shape[1]] = c
        return x0, y0, s

    def _log(self):
        return re.sub(r"\[[#.]+\]\s*", "", self.log_line.strip())      # drop the console's ASCII progress bar

    def _legend(self, img, legend, x_right, y):
        x = x_right
        for label, col in reversed(legend):
            self.text((x, y - 8), label, "caps", 14, DIM, "ra")
            x -= 9 * len(label) + 14
            cv2.circle(img, (x, y), 5, col, -1, cv2.LINE_AA)
            x -= 22

    def steps(self, img, stage, x, y, gap=18, centred=False):
        """Nine small dots: done = amber, current = amber with a ring, upcoming = dim ring."""
        if stage is None:
            return
        if centred:
            x = int(x - (self.n_steps - 1) * gap / 2)
        for k in range(self.n_steps):
            cx = x + k * gap
            if k < stage:
                cv2.circle(img, (cx, y), 4, AMBER, -1, cv2.LINE_AA)
            elif k == stage:
                cv2.circle(img, (cx, y), 5, AMBER, -1, cv2.LINE_AA)
                cv2.circle(img, (cx, y), 8, AMBER, 1, cv2.LINE_AA)
            else:
                cv2.circle(img, (cx, y), 4, (90, 90, 90), 1, cv2.LINE_AA)

    def chrome(self, img, stage, title, detail, progress, readouts, legend=None):
        return (self._chrome_vertical if self.fmt == "vertical" else self._chrome_landscape)(
            img, stage, title, detail, progress, readouts, legend)

    def _legend_centred(self, img, legend, cx, y):
        widths = [9 * len(lbl) + 14 + 22 for lbl, _ in legend]
        x = int(cx - sum(widths) / 2)
        for (label, col), wd in zip(legend, widths):
            cv2.circle(img, (x + 5, y), 5, col, -1, cv2.LINE_AA)
            self.text((x + 16, y), label, "caps", 14, DIM, "lm")
            x += wd

    def _readouts_centred(self, readouts, cx, y, col_w, label_size, value_size, value_dy):
        n = len(readouts)
        x0 = cx - col_w * (n - 1) / 2
        for k, (label, value) in enumerate(readouts):
            x = x0 + k * col_w
            self.text((x, y), label.upper(), "caps", label_size, DIM, "ma")
            self.text((x, y + value_dy), value, "mono", value_size, WHITE, "ma")

    def _chrome_landscape(self, img, stage, title, detail, progress, readouts, legend):
        """Everything centred: step label, dots, title, detail (and legend) on top; readouts, progress, log below."""
        W, H = self.W, self.H
        cx = W // 2
        ov = img.copy()
        cv2.rectangle(ov, (0, 0), (W, 142), (0, 0, 0), -1)
        cv2.rectangle(ov, (0, H - 128), (W, H), (0, 0, 0), -1)
        img = cv2.addWeighted(ov, 0.6, img, 0.4, 0)
        self.text((cx, 16), step_label(stage, self.n_steps, self.name), "caps", 16, AMBER, "ma")
        self.steps(img, stage, cx, 45, 18, centred=True)
        self.text((cx, 58), title, "title", 34, WHITE, "ma")
        self.text((cx, 100), detail, "title", 18, (215, 215, 215), "ma")
        if legend:
            self._legend_centred(img, legend, cx, 134)
        if progress is not None:
            x0, x1, py = 360, W - 360, H - 106
            cv2.line(img, (x0, py), (x1, py), GREY, 1, cv2.LINE_AA)
            px = x0 + int((x1 - x0) * min(1.0, max(0.0, progress)))
            cv2.line(img, (x0, py), (px, py), AMBER, 2, cv2.LINE_AA)
            cv2.circle(img, (px, py), 5, AMBER, -1, cv2.LINE_AA)
            self.text((x1 + 16, py), f"{progress * 100:.0f} %", "mono", 18, AMBER, "lm")
        self._readouts_centred(readouts, cx, H - 88, 300, 14, 20, 20)
        if self.log_line:
            self.text((cx, H - 24), self._log()[:170], "mono", 14, GREY, "ma")
        return img

    def _chrome_vertical(self, img, stage, title, detail, progress, readouts, legend):
        """9:16, centred. Safe zone: y 200–1700, x 60–960 (the platform's buttons sit on the right)."""
        cx = 510                                                 # centre of the safe zone
        self.text((cx, 214), step_label(stage, self.n_steps, self.name), "caps", 20, AMBER, "ma")
        self.steps(img, stage, cx, 252, 22, centred=True)
        self.text((cx, 268), title, "title", 48, WHITE, "ma")
        self.text((cx, 330), detail, "title", 23, (215, 215, 215), "ma")
        if legend:
            self._legend_centred(img, legend, cx, 378)
        y = self.H - self.bottom + 40
        rs = readouts[:4]
        for k, (label, value) in enumerate(rs):
            x = cx + (-225 if k % 2 == 0 else 225) if len(rs) > 1 else cx
            yy = y + (k // 2) * 72
            self.text((x, yy), label.upper(), "caps", 17, DIM, "ma")
            self.text((x, yy + 24), value, "mono", 27, WHITE, "ma")
        py = y + 2 * 72 + 26
        if progress is not None:
            x0, x1 = 150, 840
            cv2.line(img, (x0, py), (x1, py), GREY, 2, cv2.LINE_AA)
            px = x0 + int((x1 - x0) * min(1.0, max(0.0, progress)))
            cv2.line(img, (x0, py), (px, py), AMBER, 3, cv2.LINE_AA)
            cv2.circle(img, (px, py), 7, AMBER, -1, cv2.LINE_AA)
            self.text((x1 + 18, py), f"{progress * 100:.0f} %", "mono", 24, AMBER, "lm")
        if self.log_line:
            self.text((cx, py + 30), self._log()[:60], "mono", 16, GREY, "ma")
        return img

    # ---------------------------------------------------------------- frames

    def card(self, stage, title, subtitle=""):
        img = self.blank()
        v = self.fmt == "vertical"
        cx, cy = self.W // 2, self.H // 2 - (120 if v else 0)
        self.text((cx, cy - (52 if v else 40)), step_label(stage, self.n_steps, self.name) if stage is not None else "", "caps", 24 if v else 20, AMBER, "mm")
        self.text((cx, cy + 8), title, "title", 64 if v else 56, WHITE, "mm")
        if subtitle:
            self.text((cx, cy + (80 if v else 64)), subtitle[:48 if v else 90], "title", 26 if v else 22, DIM, "mm")
        gap = 26 if v else 22
        self.steps(img, stage, cx - (self.n_steps - 1) * gap // 2, cy + (140 if v else 118), gap)
        return self._flush(img)

    def final(self, img_bgr, title, detail, facts=()):
        """The finished mosaic in the same place as every step, with a centred minimal caption."""
        v = self.fmt == "vertical"
        img = self.blank()
        self.place(img, img_bgr)          # same image area as every step, so fades from the last step are seamless
        img = self.vignette(img, 0.3)
        cx = 510 if v else self.W // 2
        self.text((cx, 214 if v else 16), self.name, "caps", 20 if v else 16, AMBER, "ma")
        self.text((cx, 250 if v else 44), title, "title", 48 if v else 34, WHITE, "ma")
        self.text((cx, 314 if v else 88), detail, "title", 23 if v else 18, (215, 215, 215), "ma")
        if v:
            y = self.H - self.bottom + 40
            for k, (label, value) in enumerate(facts[:4]):
                x, yy = cx + (-225 if k % 2 == 0 else 225), y + (k // 2) * 72
                self.text((x, yy), label.upper(), "caps", 17, DIM, "ma")
                self.text((x, yy + 24), value, "mono", 27, WHITE, "ma")
        else:
            self._readouts_centred(list(facts), cx, self.H - 88, 300, 14, 20, 20)
        return self._flush(img)

    def compare(self, before, after, t, stage, title, detail, progress, readouts):
        """Before/after wipe: left of the line = after, right = before; t in 0..1 moves the line left to right."""
        h, w = after.shape[:2]
        x = int(round(w * t))
        img_c = before.copy()
        img_c[:, :x] = after[:, :x]
        img = self.blank()
        x0, y0, s = self.place(img, img_c)
        wp, hp = int(w * s), int(h * s)
        lx = x0 + int(round(x * s))
        if 0 < x < w:
            cv2.line(img, (lx, y0), (lx, y0 + hp), AMBER, 2, cv2.LINE_AA)
        img = self.vignette(img, 0.3)
        img = self.chrome(img, stage, title, detail, progress, readouts)
        if x > 60:
            self.text((max(x0 + 40, lx - 20), y0 + 16), "AFTER", "caps", 16, AMBER, "ra")
        if x < w - 60:
            self.text((min(x0 + wp - 40, lx + 20), y0 + 16), "BEFORE", "caps", 16, DIM, "la")
        return self._flush(img)

    def features(self, nat8, kps, stage, title, detail, progress, readouts, fade=0.0):
        """Feature detection explained: kps = [(x, y, radius, t)] in proxy pixels, t in 0..1 = small..large scale.
        Thin rings sized by the feature's scale; small ones pale, large ones amber. No glow.
        fade: 0..1 fades the panel and its rings to black (the header and readouts stay)."""
        img = self.blank()
        x0, y0, s = self.place(img, (nat8 * 0.9).astype(np.uint8))
        layer = np.zeros_like(img)
        pale, deep = np.array((205, 225, 245), float), np.array(AMBER, float)
        for x, y, r, t in kps:
            col = tuple(int(v) for v in pale * (1 - t) + deep * t)
            cv2.circle(layer, (int(x * s) + x0, int(y * s) + y0), max(1, int(round(r * s))), col, 1, cv2.LINE_AA)
        img = cv2.addWeighted(img, 1.0, layer, 0.8, 0)
        img = self.vignette(img, 0.35)
        if fade > 0:
            img = (img.astype(np.float32) * (1.0 - fade)).astype(np.uint8)
        return self._flush(self.chrome(img, stage, title, detail, progress, readouts))

    def contributions(self, img_bgr, labels, rows, stage, title, detail, progress, readouts, more=0):
        """Contribution map with the Moon in the same place as every other step (and the final shot), panel ids
        on the disk and the source files listed beside it (landscape: right side; vertical: below)."""
        v = self.fmt == "vertical"
        img = self.blank()
        x0, y0, s = self.place(img, img_bgr)
        img = self.vignette(img, 0.3)
        img = self.chrome(img, stage, title, detail, progress, [] if v else readouts)
        for x, y, text, col in labels:
            self.text((x * s + x0, y * s + y0), text, "mono", 12 if not v else 13, (235, 235, 235), "mm")
        if v:
            y = self.H - self.bottom + 36
            lines = [f"{share:4.1f} %  {name}"[:80] for share, name, col in rows[:5]]
            bw = max(len(t) for t in lines) * 9 + 24 if lines else 0
            lx = 510 - bw // 2                                   # the list is centred as a block
            for (share, name, col), t in zip(rows[:5], lines):
                cv2.rectangle(img, (lx, y + 5), (lx + 12, y + 17), col, -1)
                self.text((lx + 24, y), t, "mono", 15, (225, 225, 225))
                y += 26
            if len(rows) > 5 or more:
                self.text((510, y), f"+ {len(rows) - 5 + more} more files", "mono", 15, DIM, "ma")
        else:
            lx = x0 + int(img_bgr.shape[1] * s) + 44
            width = self.W - 48 - lx
            chars = max(12, int(width / 7.6))
            self.text((lx + (self.W - 48 - lx) // 2, self.top - 4), "SOURCE FILES", "caps", 14, DIM, "ma")
            y = self.top + 20
            max_rows = int((self.H - self.bottom - y) / 19) - 1
            for share, name, col in rows[:max_rows]:
                cv2.rectangle(img, (lx, y + 4), (lx + 9, y + 13), col, -1)
                self.text((lx + 16, y), f"{share:4.1f} %  {name}"[:chars], "mono", 12, (225, 225, 225))
                y += 19
            if len(rows) > max_rows or more:
                self.text((lx + 16, y), f"+ {len(rows) - max_rows + more} more files", "mono", 12, DIM)
        return self._flush(img)

    def keypoints(self, nat8, pts, stage, title, detail, progress, readouts):
        img = self.blank()
        x0, y0, s = self.place(img, (nat8 * 0.92).astype(np.uint8))
        layer = np.zeros_like(img)
        r = 1 if s < 1.2 else 2                               # small dots, soft glow (your feedback 2026-09-27)
        for x, y in pts:
            cv2.circle(layer, (int(x * s) + x0, int(y * s) + y0), r, (50, 135, 210), -1, cv2.LINE_AA)
        img = cv2.addWeighted(img, 1.0, layer, 0.75, 0)
        img = self.vignette(self.glow(img, layer, sigma=1.5, strength=0.3), 0.35)
        return self._flush(self.chrome(img, stage, title, detail, progress, readouts))

    def pair(self, natA, natB, pts_a, pts_b, stage, title, detail, progress, readouts, max_lines=90,
             frac=1.0, kp_a=None, kp_b=None, poly_a=None, poly_b=None):
        """Two overlapping panels and their verified matches.
        frac: share of the match threads drawn (for a progressive reveal); kp_a/kp_b: all keypoints (faint dots);
        poly_a / poly_b: the other panel's outline mapped into this panel (where the matches say they overlap)."""
        gap = 10
        vertical = self.fmt == "vertical"
        if vertical:                                           # stack: A above B, threads run downwards
            both = np.zeros((natA.shape[0] + natB.shape[0] + gap, max(natA.shape[1], natB.shape[1])), np.uint8)
            both[:natA.shape[0], :natA.shape[1]] = natA
            both[natA.shape[0] + gap:, :natB.shape[1]] = natB
            off = (0, natA.shape[0] + gap)
        else:
            both = np.zeros((max(natA.shape[0], natB.shape[0]), natA.shape[1] + natB.shape[1] + gap), np.uint8)
            both[:natA.shape[0], :natA.shape[1]] = natA
            both[:natB.shape[0], natA.shape[1] + gap:] = natB
            off = (natA.shape[1] + gap, 0)
        img = self.blank()
        x0, y0, s = self.place(img, (both * 0.85).astype(np.uint8))
        P = lambda x, y, o=(0, 0): (int((x + o[0]) * s) + x0, int((y + o[1]) * s) + y0)
        faint = np.zeros_like(img)
        for kps, o in ((kp_a, (0, 0)), (kp_b, off)):
            if kps is not None:
                for x, y in kps[::2]:
                    cv2.circle(faint, P(x, y, o), 1, (170, 170, 170), -1, cv2.LINE_AA)
        img = cv2.addWeighted(img, 1.0, faint, 0.45, 0)
        lines = np.zeros_like(img)
        dots = np.zeros_like(img)
        step = max(1, len(pts_a) // max_lines)
        sel = list(zip(pts_a[::step], pts_b[::step]))
        sel.sort(key=lambda ab: ab[0][1] if not vertical else ab[0][0])        # reveal in a sweep across the panel
        for (xa, ya), (xb, yb) in sel[:int(round(len(sel) * frac))]:
            p1, p2 = P(xa, ya), P(xb, yb, off)
            cv2.line(lines, p1, p2, (120, 120, 120), 1, cv2.LINE_AA)
            cv2.circle(dots, p1, 2, AMBER, -1, cv2.LINE_AA)
            cv2.circle(dots, p2, 2, AMBER, -1, cv2.LINE_AA)
        img = cv2.addWeighted(img, 1.0, lines, 0.4, 0)
        fill = np.zeros_like(img)
        outl = np.zeros_like(img)
        for poly, o in ((poly_a, (0, 0)), (poly_b, off)):
            if poly is not None:
                q = np.array([P(x, y, o) for x, y in poly], np.int32)
                cv2.fillPoly(fill, [q], AMBER, cv2.LINE_AA)
                cv2.polylines(outl, [q], True, AMBER, 1, cv2.LINE_AA)
        img = cv2.addWeighted(img, 1.0, fill, 0.12, 0)          # the shared terrain, lightly tinted
        img = cv2.addWeighted(img, 1.0, outl, 0.75, 0)
        img = self.vignette(self.glow(img, dots, sigma=2, strength=0.6), 0.3)
        return self._flush(self.chrome(img, stage, title, detail, progress, readouts))

    def layout(self, comp8, outlines, stage, title, detail, progress, readouts, legend=None):
        """outlines: list of (quad in comp8 pixel coords, BGR colour, thickness). Lines are drawn at frame
        resolution after placing the composite, so they stay crisp."""
        img = self.blank()
        x0, y0, s = self.place(img, comp8)
        layer = np.zeros_like(img)
        for q, col, t in outlines:
            qq = (np.asarray(q, np.float64) * s + [x0, y0]).astype(np.int32)
            cv2.polylines(layer, [qq], True, col, t, cv2.LINE_AA)
        img = cv2.addWeighted(img, 1.0, layer, 0.55, 0)
        img = self.vignette(self.glow(img, layer, sigma=3, strength=0.25), 0.3)
        return self._flush(self.chrome(img, stage, title, detail, progress, readouts, legend))
