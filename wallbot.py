"""
wallbot - generatore di muri esagonali (pannello 1920x1920, 3 colori).

Esempi:
  python wallbot.py                         1 muro, colori e layout random
  python wallbot.py -n 20                   20 muri random
  python wallbot.py -c ff7cd8 ff00b4 00ffff colori scelti (base, A, B)
  python wallbot.py -c ffff00 -n 5          base fissa, A e B random
  python wallbot.py --layout original       layout di un preset in layouts/
  python wallbot.py --from-image muro.png   riusa il layout di un muro esistente
  python wallbot.py --mask                  esporta anche la maschera RGB per UEFN
  python wallbot.py --ask                   modalita' a domande
"""
import argparse
import colorsys
import json
import math
import random
import sys
from collections import Counter
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
LAYOUT_DIR = HERE / "layouts"

# Geometria misurata sui muri di riferimento (canvas 1920).
REF = 1920
HEX_R = 119.0             # raggio esagono (flat-top)
COL_STEP = 178.5          # 1.5 * R
ROW_STEP = 206.0          # sqrt(3) * R
PANEL_INSET = 65
PANEL_RADIUS = 150
LINE_W = 2
LINE_COLOR = "64748b"
COLS = range(-5, 6)
ROWS = range(-6, 6)
SS = 2                    # supersampling per l'antialias


# ---------------------------------------------------------------- griglia

def cell_center(k, j):
    """Centro della cella (colonna k, riga j) in coordinate canvas 1920."""
    return (REF / 2 + k * COL_STEP,
            REF / 2 + j * ROW_STEP + (ROW_STEP / 2 if k % 2 else 0))


def is_paintable(k, j):
    """Solo gli esagoni interi dentro il pannello: il bordo resta colore base."""
    x, y = cell_center(k, j)
    lo, hi = PANEL_INSET, REF - PANEL_INSET
    return (x - HEX_R >= lo and x + HEX_R <= hi
            and y - ROW_STEP / 2 >= lo and y + ROW_STEP / 2 <= hi)


PAINTABLE = [(k, j) for k in COLS for j in ROWS if is_paintable(k, j)]


def neighbors(k, j):
    side = (0, 1) if k % 2 else (-1, 0)
    out = [(k, j - 1), (k, j + 1)]
    out += [(k + dk, j + dj) for dk in (-1, 1) for dj in side]
    return [c for c in out if is_paintable(*c)]


# ---------------------------------------------------------------- layout

def random_layout(rng, density=0.42):
    """Macchie di celle contigue, alternate fra classe 1 (A) e classe 2 (B)."""
    layout = {}
    target = int(len(PAINTABLE) * density)
    cls = rng.choice((1, 2))
    guard = 0
    while len(layout) < target and guard < 200:
        guard += 1
        free = [c for c in PAINTABLE if c not in layout]
        if not free:
            break
        blob = [rng.choice(free)]
        for _ in range(rng.randint(2, 6)):
            grow = [n for c in blob for n in neighbors(*c)
                    if n not in layout and n not in blob]
            if not grow:
                break
            blob.append(rng.choice(grow))
        for c in blob:
            layout[c] = cls
        cls = 3 - cls
    return layout


def layout_from_image(path):
    """Legge il layout da un muro esistente campionando il centro di ogni cella."""
    im = Image.open(path).convert("RGB")
    sx, sy = im.width / REF, im.height / REF
    px = {c: im.getpixel((int(cell_center(*c)[0] * sx), int(cell_center(*c)[1] * sy)))
          for c in PAINTABLE}
    # il bordo del pannello e' sempre colore base
    base = im.getpixel((int(REF / 2 * sx), int((PANEL_INSET + 20) * sy)))
    others = [col for col, _ in Counter(px.values()).most_common() if col != base]
    if len(others) > 2:
        print(f"! {path}: trovati {len(others) + 1} colori, tengo i 3 principali")
    rank = {col: i + 1 for i, col in enumerate(others[:2])}
    layout = {c: rank[col] for c, col in px.items() if col in rank}
    colors = [to_hex(base)] + [to_hex(c) for c in others[:2]]
    return layout, colors


def save_layout(layout, name):
    LAYOUT_DIR.mkdir(exist_ok=True)
    path = LAYOUT_DIR / f"{name}.json"
    path.write_text(json.dumps(sorted([k, j, v] for (k, j), v in layout.items())))
    return path


def load_layout(name):
    path = Path(name)
    if not path.is_file():
        path = LAYOUT_DIR / f"{name}.json"
    if not path.is_file():
        have = ", ".join(p.stem for p in LAYOUT_DIR.glob("*.json")) or "nessuno"
        sys.exit(f"Layout '{name}' non trovato. Preset disponibili: {have}")
    return {(k, j): v for k, j, v in json.loads(path.read_text())}


# ---------------------------------------------------------------- colori

def parse_hex(s):
    s = s.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        raise argparse.ArgumentTypeError(f"colore non valido: {s!r} (usa RRGGBB)")
    try:
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        raise argparse.ArgumentTypeError(f"colore non valido: {s!r} (usa RRGGBB)")


def to_hex(rgb):
    return "%02x%02x%02x" % tuple(rgb[:3])


def hsv(h, s, v):
    return tuple(round(c * 255) for c in colorsys.hsv_to_rgb(h % 1.0, s, v))


def random_palette(rng, fixed=()):
    """Palette (base, A, B). I colori in `fixed` vengono tenuti, il resto e' generato."""
    if fixed:
        h = colorsys.rgb_to_hsv(*(c / 255 for c in fixed[0]))[0]
    else:
        h = rng.random()
    scheme = rng.choice(("complementare", "triade", "analogo", "split"))
    off_a, off_b = {
        "complementare": (0.0, 0.5),      # A = base piu' carica, B = opposto
        "triade": (1 / 3, 2 / 3),
        "analogo": (0.08, -0.08),
        "split": (0.42, 0.58),
    }[scheme]
    base = hsv(h, rng.uniform(0.45, 1.0), rng.uniform(0.85, 1.0))
    a = hsv(h + off_a, rng.uniform(0.85, 1.0), rng.uniform(0.6, 1.0))
    b = hsv(h + off_b, rng.uniform(0.7, 1.0), rng.uniform(0.85, 1.0))
    if scheme == "complementare":
        base = hsv(h, rng.uniform(0.35, 0.6), 1.0)
    out = list(fixed) + [base, a, b][len(fixed):]
    return out[:3]


def palette_from_weights(colors):
    """Palette (base, A, B) da una lista [r, g, b, peso]: i colori che coprono piu' superficie.

    Base = tinta dominante, A e B = le tinte successive ben distinte. Se la skin e' quasi tutta
    neutra (nero, bianco, grigio) esce un tema chiaro/scuro con un solo colore d'accento.
    """
    bins = 36
    rows = []
    for r, g, b, w in colors:
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        rows.append((h, s, v, w))
    total = sum(w for *_, w in rows) or 1.0
    vivid = [(h, s, v, w) for h, s, v, w in rows if s > 0.25 and v > 0.18]
    white = sum(w for h, s, v, w in rows if v > 0.8 and s < 0.15) / total
    dark = sum(w for h, s, v, w in rows if v < 0.25) / total

    score = [0.0] * bins
    for h, s, v, w in vivid:
        score[int(h * bins) % bins] += w * (0.35 + s)
    smooth = [score[i] + 0.5 * (score[i - 1] + score[(i + 1) % bins]) for i in range(bins)]

    picked = []                                    # (tinta, saturazione, valore)
    for i in sorted(range(bins), key=lambda i: -smooth[i]):
        if len(picked) == 3 or smooth[i] <= 0:
            break
        if picked and smooth[i] < 0.05 * smooth_top:
            break
        if any(min(abs(i - j), bins - abs(i - j)) < 4 for j, _ in picked):
            continue
        near = [(h, s, v, w) for h, s, v, w in vivid
                if min(abs(int(h * bins) % bins - i), bins - abs(int(h * bins) % bins - i)) <= 1]
        wsum = sum(w for *_, w in near) or 1.0
        x = sum(math.cos(2 * math.pi * h) * w for h, s, v, w in near)
        y = sum(math.sin(2 * math.pi * h) * w for h, s, v, w in near)
        hue = (math.atan2(y, x) / (2 * math.pi)) % 1.0
        if not picked:
            smooth_top = smooth[i]
        picked.append((i, (hue, sum(s * w for h, s, v, w in near) / wsum, sum(v * w for h, s, v, w in near) / wsum)))
    tones = [t for _, t in picked]

    vivid_share = sum(w for *_, w in vivid) / total
    if not tones or vivid_share < 0.06:            # skin neutra
        accent = hsv(tones[0][0], 0.85, 1.0) if tones else (0, 208, 255)
        return [(232, 232, 240), (28, 28, 34), accent]

    h0, s0, _ = tones[0]
    out = [hsv(h0, max(0.55, min(s0, 0.85)), 1.0)]
    if len(tones) > 1:
        h1, s1, v1 = tones[1]
        out.append(hsv(h1, max(0.7, s1), max(0.55, min(v1, 0.8))))
    if len(tones) > 2:
        h2, s2, _ = tones[2]
        out.append(hsv(h2, max(0.7, s2), 1.0))
    # tinte mancanti: prima i neutri davvero presenti sulla skin, poi variazioni della base
    extras = []
    if dark > 0.12:
        extras.append((26, 26, 31))
    if white > 0.12:
        extras.append((255, 255, 255))
    extras += [hsv(h0, 0.9, 0.55), hsv(h0 + 0.5, 0.8, 1.0)]
    for col in extras:
        if len(out) < 3:
            out.append(col)
    return out[:3]


def skin_palette(paths):
    """Palette (base, A, B) dalle texture colore di una skin (file o cartelle con *_D.png).

    Ripiego quando non c'e' la misura sulla superficie fatta dal convertitore: qui ogni pixel
    della texture pesa uguale, anche se sulla skin copre poco.
    """
    files = []
    for p in map(Path, paths):
        files += sorted(p.rglob("*_D.png")) if p.is_dir() else [p]
    if not files:
        raise ValueError("nessuna texture colore trovata")
    counts = Counter()
    for f in files:
        im = Image.open(f).convert("RGBA").resize((96, 96), Image.BOX)
        for r, g, b, a in im.get_flattened_data() if hasattr(im, "get_flattened_data") else im.getdata():
            if a > 128:
                counts[(r >> 3, g >> 3, b >> 3)] += 1
    if not counts:
        raise ValueError("texture vuote")
    return palette_from_weights([(r * 8 + 4, g * 8 + 4, b * 8 + 4, n) for (r, g, b), n in counts.items()])


# ---------------------------------------------------------------- render

def hex_points(cx, cy, r, f):
    return [((cx + r * math.cos(math.radians(a))) * f,
             (cy + r * math.sin(math.radians(a))) * f) for a in range(0, 360, 60)]


def panel_mask(size):
    f = size * SS / REF
    m = Image.new("L", (size * SS, size * SS), 0)
    ImageDraw.Draw(m).rounded_rectangle(
        [PANEL_INSET * f, PANEL_INSET * f, (REF - PANEL_INSET) * f, (REF - PANEL_INSET) * f],
        radius=PANEL_RADIUS * f, fill=255)
    return m


def draw_cells(size, layout, fills, line, bg):
    """fills = colori per classe 0/1/2. Ritorna l'immagine RGBA a risoluzione finale."""
    f = size * SS / REF
    big = size * SS
    im = Image.new("RGB", (big, big), fills[0])
    d = ImageDraw.Draw(im)
    cells = [(k, j) for k in COLS for j in ROWS]
    for c in cells:
        d.polygon(hex_points(*cell_center(*c), HEX_R, f), fill=fills[layout.get(c, 0)])
    w = max(1, round(LINE_W * f))
    for c in cells:
        pts = hex_points(*cell_center(*c), HEX_R, f)
        d.line(pts + [pts[0]], fill=line, width=w)
    out = Image.new("RGBA", (big, big), (bg + (255,)) if bg else (0, 0, 0, 0))
    out.paste(im, (0, 0), panel_mask(size))
    return out.resize((size, size), Image.LANCZOS)


def render_wall(size, layout, colors, line, bg):
    return draw_cells(size, layout, colors, line, bg)


def render_mask(size, layout):
    """Maschera per master material UEFN: R = celle A, G = celle B, B = linee, A = pannello."""
    im = draw_cells(size, layout, [(0, 0, 0), (255, 0, 0), (0, 255, 0)], (0, 0, 255), None)
    return im


# ---------------------------------------------------------------- main

def ask(args):
    print("wallbot - invio = valore di default / random")
    n = input("Quanti muri? [1] ").strip()
    args.count = int(n) if n else 1
    c = input("Colori base A B in hex (0-3 valori, i mancanti sono random): ").split()
    args.colors = [parse_hex(x) for x in c[:3]]
    presets = ", ".join(p.stem for p in LAYOUT_DIR.glob("*.json"))
    l = input(f"Layout [random] ({presets}): ").strip()
    args.layout = l or "random"
    b = input("Sfondo hex [trasparente]: ").strip()
    args.bg = parse_hex(b) if b else None
    args.mask = input("Esportare anche la maschera UEFN? [s/N] ").strip().lower().startswith("s")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-n", "--count", type=int, default=1, help="quanti muri generare")
    ap.add_argument("-c", "--colors", type=parse_hex, nargs="*", default=[],
                    metavar="HEX", help="base, A, B (i mancanti sono random)")
    ap.add_argument("--layout", default="random", help="random, nome preset o file .json")
    ap.add_argument("--from-image", metavar="PNG", help="riusa il layout di un muro esistente")
    ap.add_argument("--save-layout", metavar="NOME", help="salva il layout usato in layouts/")
    ap.add_argument("--density", type=float, default=0.42, help="quota di celle colorate nel layout random")
    ap.add_argument("--bg", type=parse_hex, default=None, help="sfondo fuori dal pannello (default trasparente)")
    ap.add_argument("--line", type=parse_hex, default=parse_hex(LINE_COLOR), help="colore delle linee")
    ap.add_argument("--size", type=int, default=REF, help="lato in pixel")
    ap.add_argument("--seed", type=int, default=None, help="seed per risultati ripetibili")
    ap.add_argument("--mask", action="store_true", help="esporta anche la maschera RGB per UEFN")
    ap.add_argument("--out", type=Path, default=HERE / "out", help="cartella di output")
    ap.add_argument("--ask", action="store_true", help="modalita' a domande")
    ap.add_argument("--skin", nargs="+", metavar="PATH",
                    help="ricava i colori dalle texture *_D.png di una skin (file o cartelle)")
    ap.add_argument("--palette-only", action="store_true", help="con --skin: stampa la palette ed esce")
    args = ap.parse_args()
    if args.ask:
        ask(args)
    if args.skin:
        args.colors = skin_palette(args.skin)
        print("Palette dalla skin:", " ".join(to_hex(c) for c in args.colors))
        if args.palette_only:
            return
    if len(args.colors) > 3:
        ap.error("al massimo 3 colori: base, A, B")

    seed = args.seed if args.seed is not None else random.randrange(1_000_000)
    args.out.mkdir(parents=True, exist_ok=True)

    fixed_layout = None
    if args.from_image:
        fixed_layout, src_colors = layout_from_image(args.from_image)
        print(f"Layout letto da {args.from_image} (colori originali: {' '.join(src_colors)})")
    elif args.layout != "random":
        fixed_layout = load_layout(args.layout)
    if args.save_layout and fixed_layout is not None:
        print("Layout salvato:", save_layout(fixed_layout, args.save_layout))

    for i in range(args.count):
        s = seed + i
        rng = random.Random(s)
        layout = fixed_layout if fixed_layout is not None else random_layout(rng, args.density)
        colors = random_palette(rng, args.colors)
        if args.save_layout and fixed_layout is None:
            save_layout(layout, f"{args.save_layout}_{s}" if args.count > 1 else args.save_layout)
        name = f"wall_{s:06d}_{'-'.join(to_hex(c) for c in colors)}"
        render_wall(args.size, layout, colors, args.line, args.bg).save(args.out / f"{name}.png")
        if args.mask:
            render_mask(args.size, layout).save(args.out / f"{name}_M.png")
        print(f"{name}.png   seed={s}  base={to_hex(colors[0])} A={to_hex(colors[1])} B={to_hex(colors[2])}")
    print(f"Fatto: {args.count} muri in {args.out}")


if __name__ == "__main__":
    main()
