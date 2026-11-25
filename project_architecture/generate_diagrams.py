"""Generate the architecture pictures and the walkthrough video, in the hand-drawn style of layer_2_harness.png.

Outputs (written next to this file):
  layer_1_rag.png              Layer 1: triage + RAG fast path
  layer_2_current.png          Layer 2: the harness as implemented today
  complete_architecture.png    End-to-end system
  architecture_animation.mp4   The three diagrams drawn step by step, with captions

The script is not a project dependency. Run it with a Python that has pillow, numpy, imageio and imageio-ffmpeg:
    python project_architecture/generate_diagrams.py            # PNGs and video
    python project_architecture/generate_diagrams.py --png-only # PNGs only (needs only pillow)
"""
import math
import random
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).resolve().parent
W, H = 2730, 1536  # design space, same aspect and size as layer_2_harness.png
SS = 2             # supersampling for smooth lines

PAPER = (250, 249, 246)
INK = (48, 48, 48)
SHADOW = (150, 150, 160)
BLUE, ORANGE, GREEN = (160, 196, 240), (246, 196, 132), (172, 222, 168)
RED, YELLOW, GREY = (238, 150, 146), (248, 226, 130), (214, 214, 214)

SUP = "/System/Library/Fonts/Supplemental/"
FONT_FILES = {"title": SUP + "Arial Narrow Bold.ttf", "body": SUP + "Arial Narrow.ttf", "hand": "/System/Library/Fonts/Noteworthy.ttc"}
_font_cache: dict = {}


def font(kind: str, size: float) -> ImageFont.FreeTypeFont:
    key = (kind, round(size))
    if key not in _font_cache:
        _font_cache[key] = ImageFont.truetype(FONT_FILES[kind], max(6, round(size)))
    return _font_cache[key]


# ---------------------------------------------------------------- drawing context
class Ctx:
    """Maps design units to pixels for one element layer."""

    def __init__(self, bbox, scale):
        self.bx0, self.by0, bx1, by1 = bbox
        self.k = scale * SS
        self.scale = scale
        self.size = (max(2, math.ceil((bx1 - self.bx0) * self.k)), max(2, math.ceil((by1 - self.by0) * self.k)))
        self.img = Image.new("RGBA", self.size, (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.img)

    def p(self, x, y):
        return ((x - self.bx0) * self.k, (y - self.by0) * self.k)

    def line(self, pts, width=3.2, color=INK, rng=None, amp=1.5, dash=None):
        """Hand-drawn polyline: points are jittered once (seeded, so frames do not flicker)."""
        out = []
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            length = math.hypot(x1 - x0, y1 - y0) or 1
            n = max(2, int(length / 45))
            nx, ny = -(y1 - y0) / length, (x1 - x0) / length
            for i in range(n):
                f = i / n
                j = rng.uniform(-amp, amp) if (rng and i) else 0
                out.append((x0 + (x1 - x0) * f + nx * j, y0 + (y1 - y0) * f + ny * j))
        out.append(pts[-1])
        px = [self.p(*q) for q in out]
        if dash:
            step = max(2, int(dash * self.k / 6))
            for i in range(0, len(px) - 1, 2):
                self.d.line(px[i:i + 2], fill=color, width=max(1, int(width * self.k)))
            return
        self.d.line(px, fill=color, width=max(1, int(width * self.k)), joint="curve")

    def rect_lines(self, x0, y0, x1, y1, rng, width=3.2, dash=False, double=True):
        for _ in range(2 if double else 1):
            e = 5
            segs = [((x0 - e, y0), (x1 + e, y0)), ((x1, y0 - e), (x1, y1 + e)), ((x1 + e, y1), (x0 - e, y1)), ((x0, y1 + e), (x0, y0 - e))]
            for a, b in segs:
                if dash:
                    self.dashed(a, b, width, rng)
                else:
                    self.line([a, b], width if _ == 0 else width * 0.55, INK, rng)

    def dashed(self, a, b, width, rng, on=26, off=16):
        length = math.hypot(b[0] - a[0], b[1] - a[1]) or 1
        ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
        pos = 0
        while pos < length:
            end = min(pos + on, length)
            self.line([(a[0] + ux * pos, a[1] + uy * pos), (a[0] + ux * end, a[1] + uy * end)], width, INK, rng, amp=0.8)
            pos += on + off

    def text(self, x, y, s, kind, size, color=INK, anchor="ma"):
        self.d.text(self.p(x, y), s, font=font(kind, size * self.k), fill=color, anchor=anchor)

    def finish(self):
        w, h = round(self.size[0] / SS), round(self.size[1] / SS)
        return self.img.resize((max(1, w), max(1, h)), Image.LANCZOS)


def wrap(text: str, kind: str, size: float, width: float) -> list[str]:
    f = font(kind, size)
    lines: list[str] = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split():
            trial = (cur + " " + word).strip()
            if f.getlength(trial) <= width or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
    return lines


# ---------------------------------------------------------------- icons
def icon(c: Ctx, name: str, x: float, y: float, s: float, rng):
    L = lambda pts, w=2.6: c.line(pts, w, INK, rng, amp=0.6)
    if name == "doc":
        L([(x, y), (x + s * .7, y), (x + s, y + s * .3), (x + s, y + s), (x, y + s), (x, y)])
        for i in range(4):
            L([(x + s * .15, y + s * (.45 + i * .13)), (x + s * .8, y + s * (.45 + i * .13))], 2)
    elif name == "browser":
        L([(x, y + s * .1), (x + s, y + s * .1), (x + s, y + s * .95), (x, y + s * .95), (x, y + s * .1)])
        L([(x, y + s * .33), (x + s, y + s * .33)], 2)
        for i in range(3):
            c.d.ellipse([*c.p(x + s * (.1 + i * .12) - 3, y + s * .2 - 3), *c.p(x + s * (.1 + i * .12) + 3, y + s * .2 + 3)], outline=INK)
    elif name == "logs":
        L([(x, y + s * .1), (x + s, y + s * .1), (x + s, y + s * .95), (x, y + s * .95), (x, y + s * .1)])
        for i in range(4):
            L([(x + s * .15, y + s * (.3 + i * .16)), (x + s * .85, y + s * (.3 + i * .16))], 2)
    elif name == "db":
        for i in range(3):
            yy = y + s * (.18 + i * .3)
            c.d.arc([*c.p(x, yy - s * .13), *c.p(x + s, yy + s * .13)], 0, 360, fill=INK, width=int(2.6 * c.k))
        L([(x, y + s * .18), (x, y + s * .78)]); L([(x + s, y + s * .18), (x + s, y + s * .78)])
    elif name == "user":
        c.d.ellipse([*c.p(x + s * .3, y), *c.p(x + s * .7, y + s * .4)], outline=INK, width=int(2.6 * c.k))
        c.d.arc([*c.p(x, y + s * .5), *c.p(x + s, y + s * 1.3)], 180, 360, fill=INK, width=int(2.6 * c.k))
    elif name == "monitor":
        L([(x, y + s * .1), (x + s, y + s * .1), (x + s, y + s * .7), (x, y + s * .7), (x, y + s * .1)])
        L([(x + s * .4, y + s * .7), (x + s * .35, y + s * .95), (x + s * .65, y + s * .95), (x + s * .6, y + s * .7)], 2)
    elif name == "shield":
        L([(x, y + s * .1), (x + s * .5, y), (x + s, y + s * .1), (x + s * .9, y + s * .65), (x + s * .5, y + s), (x + s * .1, y + s * .65), (x, y + s * .1)])
        L([(x + s * .3, y + s * .5), (x + s * .45, y + s * .65), (x + s * .72, y + s * .3)], 2.6)
    elif name == "api":
        L([(x + s * .1, y + s * .1), (x + s * .9, y + s * .1), (x + s * .9, y + s * .9), (x + s * .1, y + s * .9), (x + s * .1, y + s * .1)])
        L([(x + s * .35, y + s * .35), (x + s * .2, y + s * .5), (x + s * .35, y + s * .65)], 2.4)
        L([(x + s * .65, y + s * .35), (x + s * .8, y + s * .5), (x + s * .65, y + s * .65)], 2.4)


# ---------------------------------------------------------------- elements
class Element:
    kind = "box"
    cap = ""
    hold = 0.0

    def bbox(self):
        raise NotImplementedError

    def padded(self, pad=44):
        x0, y0, x1, y1 = self.bbox()
        return (x0 - pad, y0 - pad, x1 + pad, y1 + pad)

    def render(self, scale, t=1.0):
        c = Ctx(self.padded(), scale)
        self.paint(c, t)
        return (round(c.bx0 * scale), round(c.by0 * scale)), c.finish()


class Box(Element):
    """Rectangle with a colored offset bar, optional icon, title and body."""

    def __init__(self, x0, y0, x1, y1, title, body="", sub="", accent=None, ico=None, seed=1, dash=False,
                 center=None, tsize=40, bsize=29, heavy=False, cap="", hold=0.0):
        self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1
        self.title, self.body, self.sub, self.accent, self.ico = title, body, sub, accent, ico
        self.seed, self.dash, self.tsize, self.bsize, self.heavy = seed, dash, tsize, bsize, heavy
        self.center = (ico is None) if center is None else center
        self.cap, self.hold = cap, hold

    def bbox(self):
        return (self.x0, self.y0, self.x1 + 16, self.y1 + 18)

    def _layout(self, width):
        ts, bs = self.tsize, self.bsize
        inner = (self.y1 - self.y0) - 36
        while True:
            tl = wrap(self.title, "title", ts, width)
            sl = wrap(self.sub, "body", bs * 0.95, width) if self.sub else []
            bl = wrap(self.body, "body", bs, width) if self.body else []
            h = len(tl) * ts * 1.12 + len(sl) * bs * 1.12 + (10 if bl else 0) + len(bl) * bs * 1.16
            if h <= inner or ts <= 20:
                return ts, bs, tl, sl, bl, h
            ts, bs = ts - 2, bs - 1.5

    def paint(self, c: Ctx, t=1.0):
        rng = random.Random(self.seed)
        x0, y0, x1, y1 = self.x0, self.y0, self.x1, self.y1
        # soft shadow
        sh = Image.new("L", c.size, 0)
        ImageDraw.Draw(sh).rectangle([*c.p(x0 + 8, y0 + 10), *c.p(x1 + 8, y1 + 10)], fill=95)
        sh = sh.filter(ImageFilter.GaussianBlur(5 * c.k))
        c.img.alpha_composite(Image.merge("RGBA", (*Image.new("RGB", c.size, SHADOW).split(), sh)))
        if self.accent:
            c.d.rectangle([*c.p(x0 + 12, y0 + 12), *c.p(x1 + 13, y1 + 15)], fill=self.accent + (255,))
        c.d.rectangle([*c.p(x0, y0), *c.p(x1, y1)], fill=PAPER + (255,))
        c.rect_lines(x0, y0, x1, y1, rng, width=4.2 if self.heavy else 3.0, dash=self.dash)
        if self.heavy:
            c.rect_lines(x0 + 12, y0 + 12, x1 - 12, y1 - 12, rng, width=2.4)
        pad = 24
        if self.ico:
            isz = 62
            icon(c, self.ico, x0 + pad, y0 + pad, isz, rng)
            tx = x0 + pad + isz + 18
            width = (x1 - pad) - tx
            ts, bs, tl, sl, bl, _ = self._layout(width)
            y = y0 + pad - 2
            for ln in tl:
                c.text(tx, y, ln, "title", ts, anchor="la"); y += ts * 1.12
            y = max(y, y0 + pad + isz) + 8
            for ln in sl:
                c.text(x0 + pad, y, ln, "body", bs * 0.95, anchor="la"); y += bs * 1.12
            for ln in bl:
                c.text(x0 + pad, y, ln, "body", bs, anchor="la"); y += bs * 1.16
            return
        width = (x1 - x0) - 2 * pad
        ts, bs, tl, sl, bl, h = self._layout(width)
        y = y0 + ((y1 - y0) - h) / 2
        cx = (x0 + x1) / 2
        for ln in tl:
            c.text(cx, y, ln, "title", ts); y += ts * 1.12
        for ln in sl:
            c.text(cx, y, ln, "body", bs * 0.95); y += bs * 1.12
        if bl:
            y += 10
        for ln in bl:
            c.text(cx, y, ln, "body", bs); y += bs * 1.16


class Diamond(Element):
    def __init__(self, cx, cy, hw, hh, title, body="", accent=YELLOW, seed=2, tsize=30, bsize=25, cap="", hold=0.0):
        self.cx, self.cy, self.hw, self.hh = cx, cy, hw, hh
        self.title, self.body, self.accent, self.seed, self.tsize, self.bsize = title, body, accent, seed, tsize, bsize
        self.cap, self.hold = cap, hold

    def bbox(self):
        return (self.cx - self.hw, self.cy - self.hh, self.cx + self.hw + 14, self.cy + self.hh + 14)

    def paint(self, c: Ctx, t=1.0):
        rng = random.Random(self.seed)
        cx, cy, hw, hh = self.cx, self.cy, self.hw, self.hh
        pts = [(cx, cy - hh), (cx + hw, cy), (cx, cy + hh), (cx - hw, cy)]
        if self.accent:
            c.d.polygon([c.p(x + 12, y + 13) for x, y in pts], fill=self.accent + (255,))
        c.d.polygon([c.p(*p) for p in pts], fill=PAPER + (255,))
        for _ in range(2):
            for a, b in zip(pts, pts[1:] + pts[:1]):
                c.line([a, b], 3.0 if _ == 0 else 1.8, INK, rng)
        width = hw * 1.05
        tl, bl = wrap(self.title, "title", self.tsize, width), wrap(self.body, "body", self.bsize, width) if self.body else []
        h = len(tl) * self.tsize * 1.1 + len(bl) * self.bsize * 1.12
        y = cy - h / 2
        for ln in tl:
            c.text(cx, y, ln, "title", self.tsize); y += self.tsize * 1.1
        for ln in bl:
            c.text(cx, y, ln, "body", self.bsize); y += self.bsize * 1.12


class Container(Element):
    def __init__(self, x0, y0, x1, y1, label, seed=3, cap="", hold=0.0):
        self.x0, self.y0, self.x1, self.y1, self.label, self.seed, self.cap, self.hold = x0, y0, x1, y1, label, seed, cap, hold

    def bbox(self):
        return (self.x0, self.y0 - 10, self.x1, self.y1)

    def paint(self, c: Ctx, t=1.0):
        rng = random.Random(self.seed)
        c.rect_lines(self.x0, self.y0, self.x1, self.y1, rng, width=2.6, dash=True, double=False)
        tw = font("title", 34 * c.k).getlength(self.label) / c.k
        c.d.rectangle([*c.p(self.x0 + 24, self.y0 - 22), *c.p(self.x0 + 24 + tw + 30, self.y0 + 22)], fill=PAPER + (255,))
        c.text(self.x0 + 39, self.y0 - 20, self.label, "title", 34, anchor="la")


class Text(Element):
    kind = "text"

    def __init__(self, cx, cy, text, size=32, kindf="hand", width=420, color=INK, cap="", hold=0.0, align="ma"):
        self.cx, self.cy, self.text_, self.size, self.kindf, self.width, self.color, self.cap, self.hold, self.align = cx, cy, text, size, kindf, width, color, cap, hold, align
        self.lines = wrap(text, kindf, size, width)

    def bbox(self):
        h = len(self.lines) * self.size * 1.25
        return (self.cx - self.width / 2, self.cy - h / 2, self.cx + self.width / 2, self.cy + h / 2)

    def paint(self, c: Ctx, t=1.0):
        h = len(self.lines) * self.size * 1.25
        y = self.cy - h / 2
        for ln in self.lines:
            c.text(self.cx, y, ln, self.kindf, self.size, self.color, anchor=self.align); y += self.size * 1.25


class Arrow(Element):
    kind = "arrow"

    def __init__(self, p0, c1, p1, label="", lpos=None, seed=5, head=True, dash=False, size=32, cap="", hold=0.0, lwidth=300):
        self.p0, self.c1, self.p1, self.label, self.lpos, self.seed, self.head, self.dash, self.size, self.cap, self.hold, self.lwidth = p0, c1, p1, label, lpos, seed, head, dash, size, cap, hold, lwidth
        self.pts = [self._at(i / 48) for i in range(49)]
        self.lines = wrap(label, "hand", size, lwidth) if label else []

    def _at(self, t):
        a, b, c = self.p0, self.c1, self.p1
        return tuple((1 - t) ** 2 * a[i] + 2 * t * (1 - t) * b[i] + t * t * c[i] for i in (0, 1))

    def bbox(self):
        xs = [p[0] for p in self.pts]; ys = [p[1] for p in self.pts]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        if self.label:
            lx, ly = self.lpos
            h = len(self.lines) * self.size * 1.25
            x0, x1 = min(x0, lx - self.lwidth / 2), max(x1, lx + self.lwidth / 2)
            y0, y1 = min(y0, ly - h / 2), max(y1, ly + h / 2)
        return (x0 - 10, y0 - 10, x1 + 10, y1 + 10)

    def paint(self, c: Ctx, t=1.0):
        rng = random.Random(self.seed)
        n = max(2, int(t * 48) + 1)
        pts = self.pts[:n]
        if len(pts) >= 2:
            jit = []
            for i, (x, y) in enumerate(pts):
                j = rng.uniform(-1.2, 1.2) if 0 < i < len(pts) - 1 else 0
                jit.append((x + j, y + j * 0.6))
            if self.dash:
                for i in range(0, len(jit) - 1, 3):
                    c.line(jit[i:i + 2], 3.0, INK, None)
            else:
                c.line(jit, 3.4, INK, None)
            if self.head:
                (x1, y1), (x0, y0) = pts[-1], pts[max(0, len(pts) - 4)]
                ang = math.atan2(y1 - y0, x1 - x0)
                for da in (0.45, -0.45):
                    c.line([(x1, y1), (x1 - 30 * math.cos(ang + da), y1 - 30 * math.sin(ang + da))], 3.4, INK, None)
        if self.label and t > 0.35:
            h = len(self.lines) * self.size * 1.25
            y = self.lpos[1] - h / 2
            for ln in self.lines:
                c.text(self.lpos[0], y, ln, "hand", self.size); y += self.size * 1.25


# ---------------------------------------------------------------- scenes
def title_box(sub):
    return Box(440, 70, 2290, 270, "RESOLVE AI  ·  AUTONOMOUS INCIDENT RESOLUTION HARNESS", sub=sub, tsize=70, bsize=50, seed=11, center=True,
               cap="Resolve AI: an autonomous incident resolution harness")


def scene_layer1():
    E = [title_box("Layer 1: RAG Fast Path (Simple Incidents)")]
    E += [
        Text(260, 625, "Pasted ticket: VIN, dealer, RDR ID, status, description", 30, width=360),
        Arrow((260, 668), (250, 690), (250, 722), seed=21, size=1, cap="A ticket arrives from the Streamlit form through POST /incidents"),
        Box(80, 725, 440, 905, "INCIDENT TICKET", "Streamlit form → FastAPI", seed=12, ico="user", cap="A ticket arrives from the Streamlit form through POST /incidents"),
        Arrow((440, 815), (520, 800), (610, 805), "parse_ticket", (525, 762), seed=22, size=28, lwidth=150),
        Diamond(800, 805, 190, 150, "LIVE TRANSACTION?", "VIN, RDR ID, submission ID or status", seed=13, cap="Triage is a plain rule: does the ticket name a live transaction?"),
        Text(800, 620, "triage_node (rule-based router)", 30, width=380),
        Arrow((990, 805), (1070, 790), (1150, 805), "NO", (1070, 755), seed=23, lwidth=60,
              cap="No live identifiers: a general question, so Layer 1 answers it from the knowledge base"),
        Box(1150, 660, 1600, 950, "LAYER 1 RAG AGENT", sub="(LangChain create_agent · Claude Haiku 5.5)",
            body="Calls search_kb first → answers only from returned passages → cites each source → never changes data",
            accent=BLUE, heavy=True, seed=14, bsize=30, cap="The Layer 1 agent searches first and answers only from cited passages"),
        Box(1150, 310, 1600, 500, "RETRIEVER + CHROMADB", "Collection layer1_kb · dense search · cosine distance ≤ 0.42 · top 4 passages", accent=ORANGE, ico="db", seed=15, bsize=27),
        Arrow((1330, 660), (1310, 600), (1330, 505), "asks", (1270, 585), seed=24),
        Arrow((1440, 505), (1465, 600), (1440, 655), "returns", (1545, 585), seed=25),
        Box(1850, 310, 2300, 520, "KNOWLEDGE BASE", "layer1_kb PDFs: product knowledge, policies, training FAQs, chunked and indexed ahead of time", accent=ORANGE, ico="doc", seed=16, bsize=27,
            cap="The knowledge base PDFs are chunked and indexed into ChromaDB ahead of time"),
        Arrow((1850, 400), (1730, 390), (1605, 400), "ingest", (1728, 345), seed=26),
        Arrow((1600, 805), (1700, 790), (1815, 805), "Layer1Result", (1710, 745), seed=27),
        Diamond(2010, 805, 190, 150, "CONFIDENT ANSWER?", "passages found and no live data needed", seed=17, cap="If a passage is close enough and no live data is needed, Layer 1 answers"),
        Arrow((2200, 805), (2290, 795), (2380, 805), "YES", (2290, 745), seed=28),
        Box(2380, 665, 2690, 945, "ANSWER + CITATIONS", "resolved = true, shown on the dashboard", accent=GREEN, seed=18, bsize=28, cap="Confident: the answer and its sources go straight to the dashboard"),
        Arrow((2010, 955), (2030, 1040), (2010, 1130), "NO", (2070, 1040), seed=29),
        Box(1810, 1130, 2210, 1330, "ESCALATE TO LAYER 2", "NO_CONFIDENT_MATCH or live data needed: resolved = false", accent=RED, seed=19, bsize=27, cap="Not confident, or live data needed: escalate"),
        Arrow((800, 955), (790, 1050), (800, 1128), "YES: live data needed", (960, 1045), seed=30, lwidth=260,
              cap="Tickets about a live transaction skip Layer 1 and go straight to Layer 2"),
        Box(620, 1130, 1000, 1330, "LAYER 2 HARNESS", "DeepAgents planner + logs, DB, browser and docs subagents", accent=BLUE, seed=20, bsize=27),
        Arrow((1810, 1230), (1400, 1250), (1002, 1232), "escalates", (1400, 1180), seed=31),
        Box(1090, 1285, 1730, 1420, "SHARED STATE", "LangGraph IncidentState carries the ticket, route and Layer 1 result", seed=32, tsize=30, bsize=26, dash=True),
    ]
    return E


def scene_layer2():
    E = [title_box("Layer 2: DeepAgents Workflow (Complex Incident Analysis, as implemented)")]
    E += [
        Text(330, 650, "Ticket: VIN, dealer, RDR ID, status, description", 30, width=330),
        Arrow((330, 695), (300, 725), (250, 757), seed=41, size=1),
        Box(74, 758, 413, 920, "ESCALATED FROM TRIAGE / LAYER 1", "(live transaction ticket)", seed=42, tsize=36, bsize=29,
            cap="A live-transaction ticket is escalated to the Layer 2 harness"),
        Arrow((413, 840), (500, 830), (583, 840), "triggers", (498, 790), seed=43, lwidth=120),
        Box(583, 700, 943, 1113, "PLANNER / ORCHESTRATOR", sub="(LangChain agent · Claude Haiku 5.5)",
            body="Plans the checks → Python policy: browser + logs + DB required, docs optional, max 2 rounds, no repeats → replans on findings → writes the RCA",
            accent=BLUE, heavy=True, seed=44, tsize=38, bsize=28,
            cap="The planner picks the checks; Python enforces the policy so the model cannot skip evidence"),
        # agents
        Box(1140, 339, 1452, 594, "DOCS AGENT", "Searches runbooks and the status/error catalog (ChromaDB). Supporting evidence only.", accent=ORANGE, ico="doc", seed=45, bsize=26,
            cap="Four specialists run in parallel, each read-only"),
        Box(1140, 631, 1452, 885, "BROWSER AGENT", "Fixed-step login + search in the RDR console. Reads status, retries, last error. Saves a screenshot.", accent=ORANGE, ico="browser", seed=46, bsize=26),
        Box(1140, 922, 1452, 1177, "LOGS AGENT", "Python searches logs by RDR ID, submission ID and VIN, follows trace IDs; the agent writes the report.", accent=ORANGE, ico="logs", seed=47, bsize=26),
        Box(1140, 1217, 1452, 1471, "DATABASE AGENT", "Pre-fetches the record (read-only SQLite). One guarded fix, only after approval.", accent=ORANGE, ico="db", seed=48, bsize=26),
        Arrow((960, 735), (1020, 520), (1140, 470), "asks", (968, 525), seed=51),
        Arrow((1140, 545), (1010, 640), (948, 700), "returns", (1090, 642), seed=52, lwidth=100),
        Arrow((943, 790), (1040, 745), (1140, 735), "asks", (1035, 710), seed=53, lwidth=80),
        Arrow((1140, 790), (1040, 825), (948, 832), "returns", (1050, 860), seed=54),
        Arrow((943, 935), (1040, 950), (1140, 985), "asks", (1030, 903), seed=55, lwidth=80),
        Arrow((1140, 1062), (1040, 1065), (948, 1010), "returns", (1050, 1100), seed=56),
        Arrow((900, 1110), (1000, 1260), (1140, 1310), "asks", (1090, 1205), seed=57, lwidth=80),
        Arrow((1140, 1405), (940, 1385), (885, 1118), "returns", (830, 1255), seed=58, lwidth=100),
        # synthesis
        Arrow((1452, 466), (1620, 520), (1700, 718), seed=59),
        Arrow((1452, 758), (1560, 770), (1625, 810), "findings", (1535, 720), seed=60, lwidth=140),
        Arrow((1452, 1050), (1580, 1010), (1632, 935), seed=61),
        Arrow((1452, 1344), (1620, 1250), (1760, 992), seed=62),
        Box(1625, 720, 1915, 990, "SYNTHESIS & RESOLUTION", "Logs + DB confirm the cause → RCA: confirmed / probable / undetermined → recommended action", accent=GREEN, seed=63, tsize=34, bsize=26,
            cap="Findings flow into synthesis; without log evidence a cause can be at most probable"),
        Box(1700, 1230, 2000, 1380, "SHARED LANGGRAPH STATE", "completed_investigations, investigation_plan, rounds", seed=64, tsize=28, bsize=25, dash=True),
        Arrow((1915, 855), (1960, 850), (1996, 835), seed=65),
        Diamond(2128, 835, 130, 105, "REQUIRES WRITE / FIX ACTION?", "", seed=66, tsize=27, cap="Does the fix need a database write?"),
        Arrow((2128, 730), (2140, 640), (2128, 582), "YES", (2190, 650), seed=67),
        Box(1937, 456, 2330, 578, "HUMAN-IN-THE-LOOP (HITL) APPROVAL", accent=RED, seed=68, tsize=34,
            cap="A write pauses the graph with a LangGraph interrupt until an engineer approves"),
        Text(2570, 600, "LangGraph interrupt, wait for engineer click", 28, width=300),
        Arrow((2128, 940), (2120, 1000), (2128, 1048), "NO / READ-ONLY", (2020, 995), seed=69, lwidth=170, size=28),
        Arrow((2330, 540), (2400, 900), (2262, 1075), "APPROVED: apply guarded UPDATE", (2535, 685), seed=70, lwidth=240, size=28),
        Box(1990, 1050, 2270, 1215, "VERIFY RESOLUTION", "fresh browser check: the dashboard row matches", accent=GREEN, seed=71, tsize=32, bsize=25,
            cap="Verify with a fresh browser check before publishing"),
        Arrow((2270, 1135), (2520, 1150), (2470, 918), "publish", (2420, 1185), seed=72, size=28),
        Box(2420, 748, 2685, 915, "POST TO DASHBOARD", "Root cause, action, evidence cards + screenshot", accent=GREEN, seed=73, tsize=32, bsize=27,
            cap="The RCA and the agents' evidence, with the screenshot, appear on the dashboard"),
        Arrow((2610, 920), (2625, 1070), (2560, 1236), seed=74, dash=True),
        Box(2290, 1240, 2690, 1465, "UPDATE EPISODIC MEMORY (planned)", "Vector DB: save the verified solution so Layer 1 can answer it next time", seed=75, tsize=34, bsize=27, dash=True,
            cap="Planned: write the verified fix to episodic memory"),
    ]
    return E


def scene_complete():
    E = [title_box("Complete Architecture: End-to-End Incident Resolution")]
    E += [
        Box(80, 320, 400, 520, "DEALER / SUPPORT ENGINEER", "Pastes the ticket, approves or rejects fixes", ico="user", seed=81, tsize=34, bsize=27,
            cap="A dealer or support engineer submits a ticket"),
        Box(520, 320, 960, 520, "STREAMLIT UI :8501", "Submit ticket · live dashboard cards · approval buttons", ico="monitor", seed=82, tsize=34, bsize=27, accent=BLUE),
        Arrow((400, 420), (460, 400), (520, 420), "ticket", (460, 365), seed=83, size=28, lwidth=110),
        Box(1080, 320, 1560, 520, "FASTAPI BACKEND :8000", "POST /incidents, GET /incidents, /screenshot, /decision. Runs in the background, max 2 at once.", ico="api", seed=84, tsize=34, bsize=25, accent=BLUE,
            cap="Streamlit talks to a FastAPI backend that starts and tracks each run"),
        Arrow((960, 385), (1020, 372), (1080, 385), "POST", (1020, 340), seed=85, size=26, lwidth=90),
        Arrow((1080, 460), (1020, 478), (960, 460), "poll 3s", (1020, 498), seed=86, size=26, lwidth=110),
        Box(1680, 320, 2100, 520, "AGENT CONTEXT", "context/AGENTS.md, subagent rules and skills/*/SKILL.md steer the agents", ico="doc", accent=ORANGE, seed=87, tsize=34, bsize=26,
            cap="Agent behavior lives in context files, not in Python"),
        Box(2220, 320, 2690, 520, "GUARDRAILS", "Read-only SQL prefetch · one guarded UPDATE (row_version check, audit log) · no write without approval", ico="shield", accent=RED, seed=88, tsize=34, bsize=25, dash=True),
        Container(520, 650, 2690, 1080, "LANGGRAPH WORKFLOW  (graph/workflow.py)", seed=89, cap="The LangGraph workflow routes every ticket"),
        Arrow((1230, 522), (1250, 590), (1260, 648), "start_ticket", (1135, 590), seed=90, size=26, lwidth=150),
        Arrow((1420, 648), (1440, 600), (1420, 522), "RCA + findings", (1560, 590), seed=91, size=26, lwidth=200),
        Diamond(700, 855, 130, 130, "TRIAGE", "live ticket?", seed=92, tsize=32, bsize=26, cap="Triage: live transaction or general question?"),
        Box(920, 720, 1220, 990, "LAYER 1 RAG", "Fast path. search_kb over ChromaDB, cites sources if distance ≤ 0.42", accent=BLUE, seed=93, tsize=34, bsize=26, cap="General questions: Layer 1 answers from the knowledge base"),
        Arrow((830, 855), (875, 845), (920, 855), "NO", (875, 808), seed=94, size=26, lwidth=60),
        Box(1340, 720, 1780, 990, "LAYER 2 HARNESS", "Planner + browser, logs, DB and docs subagents run in parallel, then synthesis → RCA", accent=BLUE, heavy=True, seed=95, tsize=36, bsize=27,
            cap="Live-transaction tickets go to the Layer 2 harness"),
        Arrow((1220, 855), (1280, 845), (1340, 855), "escalate", (1280, 808), seed=96, size=24, lwidth=100),
        Arrow((700, 985), (1030, 1100), (1340, 960), "YES: live transaction", (665, 1040), seed=97, size=26, lwidth=240),
        Box(1900, 720, 2140, 990, "HITL APPROVAL", "LangGraph interrupt, only when a DB write is proposed", accent=RED, seed=98, tsize=32, bsize=26, cap="Only a proposed database write waits for human approval"),
        Arrow((1780, 855), (1840, 845), (1900, 855), "write?", (1840, 808), seed=99, size=24, lwidth=90),
        Box(2250, 720, 2650, 990, "VERIFY → PUBLISH", "Fresh browser check, then result and evidence cards on the dashboard", accent=GREEN, seed=100, tsize=34, bsize=26),
        Arrow((2140, 855), (2195, 845), (2250, 855), "ok", (2195, 810), seed=101, size=24, lwidth=60),
        Arrow((1760, 990), (1990, 1090), (2400, 992), "read-only: skip approval", (2080, 1056), seed=102, size=26, lwidth=260),
        # data row
        Box(80, 1190, 460, 1460, "KNOWLEDGE BASE", "layer1_kb/ PDFs (policies, product, FAQs) + knowledge_base/ runbooks (status catalog, 3 runbooks)", ico="doc", accent=ORANGE, seed=103, tsize=32, bsize=25,
            cap="Underneath: the knowledge base, the vector store, the RDR database and the logs"),
        Box(518, 1190, 898, 1460, "CHROMADB", "data/vector_store. layer1_kb collection for Layer 1, runbook index for the docs agent", ico="db", accent=ORANGE, seed=104, tsize=30, bsize=25),
        Box(956, 1190, 1336, 1460, "RDR DATABASE", "data/rdr.db. SQLite rdr_records: status, state, retries, last error, row_version", ico="db", accent=ORANGE, seed=105, tsize=30, bsize=25),
        Box(1394, 1190, 1774, 1460, "APPLICATION LOGS", "logs/RDR. JSON lines with trace IDs from the edge, API, validation, worker and vehicle-master services", ico="logs", accent=ORANGE, seed=106, tsize=30, bsize=25),
        Box(1832, 1190, 2212, 1460, "RDR SUPPORT CONSOLE", "web/ on :8765. Login + transaction grid. The system the browser agent inspects", ico="browser", accent=ORANGE, seed=107, tsize=30, bsize=25),
        Box(2270, 1190, 2650, 1460, "EVIDENCE & AUDIT", "data/screenshots/*.jpg, db_changes.jsonl audit log, checkpoints.db", ico="doc", accent=ORANGE, seed=108, tsize=30, bsize=25),
        Arrow((1000, 990), (840, 1090), (720, 1188), "search_kb", (915, 1118), seed=109, size=24, lwidth=120),
        Arrow((1390, 990), (1250, 1090), (1146, 1188), "prefetch", (1300, 1128), seed=110, size=24, lwidth=110),
        Arrow((1530, 990), (1560, 1090), (1584, 1188), "log search", (1690, 1128), seed=111, size=24, lwidth=130),
        Arrow((1650, 990), (1790, 1090), (1960, 1188), "browser reads grid", (2020, 1108), seed=112, size=24, lwidth=190),
        Arrow((2440, 990), (2470, 1090), (2460, 1188), "evidence", (2560, 1100), seed=113, size=24, lwidth=120),
        Arrow((1860, 520), (1760, 590), (1640, 722), "rules + skills", (1850, 600), seed=114, size=26, lwidth=160),
    ]
    return E


def scene_intro():
    return [
        Box(500, 300, 2230, 560, "RESOLVE AI", sub="Autonomous Incident Resolution Harness · architecture walkthrough", tsize=120, bsize=52, seed=201, heavy=True, accent=BLUE),
        Box(500, 680, 1060, 860, "1. LAYER 1", "RAG fast path for simple tickets", accent=ORANGE, seed=202, tsize=44, bsize=34),
        Box(1085, 680, 1645, 860, "2. LAYER 2", "DeepAgents harness for complex tickets", accent=GREEN, seed=203, tsize=44, bsize=34),
        Box(1670, 680, 2230, 860, "3. COMPLETE SYSTEM", "UI, API, workflow, data, guardrails", accent=RED, seed=204, tsize=44, bsize=34),
    ]


SCENES = {"layer_1_rag": scene_layer1, "layer_2_current": scene_layer2, "complete_architecture": scene_complete}


# ---------------------------------------------------------------- rendering
def compose_static(elements, scale=1.0, size=(W, H)):
    img = Image.new("RGBA", (round(size[0] * scale), round(size[1] * scale)), PAPER + (255,))
    for el in elements:
        (x, y), layer = el.render(scale)
        img.alpha_composite(layer, (x, y)) if x >= 0 and y >= 0 else img.paste(layer, (x, y), layer)
    return img.convert("RGB")


def paste(base: Image.Image, pos, layer: Image.Image, alpha=1.0, dy=0):
    if alpha < 1.0:
        layer = layer.copy()
        layer.putalpha(ImageChops.multiply(layer.getchannel("A"), Image.new("L", layer.size, int(255 * alpha))))
    x, y = pos[0], pos[1] + dy
    sx, sy = max(0, -x), max(0, -y)
    if sx or sy:
        layer = layer.crop((sx, sy, layer.width, layer.height))
    base.alpha_composite(layer, (max(0, x), max(0, y)))


def ease(t):
    return 1 - (1 - t) ** 3


def caption_overlay(frame: Image.Image, text: str, scale: float):
    if not text:
        return
    ov = Image.new("RGBA", frame.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    f = font("hand", 40 * scale)
    w = f.getlength(text)
    cx, y = frame.width / 2, frame.height - 92 * scale
    d.rounded_rectangle([cx - w / 2 - 30 * scale, y - 10 * scale, cx + w / 2 + 30 * scale, y + 78 * scale], radius=18 * scale, fill=(32, 32, 36, 225))
    d.text((cx, y + 2 * scale), text, font=f, fill=(255, 255, 255, 255), anchor="ma")
    frame.alpha_composite(ov)


def render_video(path: Path, fps=15, vw=1920):
    import imageio.v2 as imageio
    import numpy as np

    scale = vw / W
    size = (round(W * scale), round(H * scale))
    writer = imageio.get_writer(str(path), fps=fps, codec="libx264", quality=8, pixelformat="yuv420p", macro_block_size=1)
    blank = Image.new("RGBA", size, PAPER + (255,))
    count = 0

    def put(frame):
        nonlocal count
        writer.append_data(np.asarray(frame.convert("RGB")))
        count += 1

    sections = [("intro", scene_intro())] + [(name, fn()) for name, fn in SCENES.items()]
    for name, elements in sections:
        base = blank.copy()
        caption = ""
        dur_map = {"box": 0.55, "arrow": 0.6, "text": 0.35}
        for el in elements:
            caption = el.cap or caption
            dur = (0.7 if isinstance(el, (Diamond, Container)) else dur_map.get(el.kind, 0.5)) + el.hold
            n = max(2, int(dur * fps))
            if el.kind == "arrow" and getattr(el, "size", 0) == 1:  # tiny leader arrows: skip the pause
                n = 3
            cache = None
            for i in range(1, n + 1):
                t = ease(i / n)
                frame = base.copy()
                if el.kind == "arrow":
                    pos, layer = el.render(scale, t)
                    paste(frame, pos, layer)
                else:
                    if cache is None:
                        cache = el.render(scale)
                    paste(frame, cache[0], cache[1], alpha=t, dy=round((1 - t) * 14 * scale))
                caption_overlay(frame, caption, scale)
                put(frame)
            pos, layer = el.render(scale) if cache is None else cache
            paste(base, pos, layer)
            for _ in range(2):
                f = base.copy(); caption_overlay(f, caption, scale); put(f)
        for _ in range(int(3.0 * fps)):
            f = base.copy(); caption_overlay(f, "", scale); put(f)
        for i in range(1, int(0.5 * fps) + 1):  # fade to paper between sections
            put(Image.blend(base, blank, i / (0.5 * fps)))
    writer.close()
    return count / fps


if __name__ == "__main__":
    for name, fn in SCENES.items():
        compose_static(fn()).save(OUT / f"{name}.png")
        print("wrote", OUT / f"{name}.png", flush=True)
    if "--png-only" not in sys.argv:
        seconds = render_video(OUT / "architecture_animation.mp4")
        print(f"wrote architecture_animation.mp4 ({seconds:.0f}s)")
