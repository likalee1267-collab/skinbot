"""Interfaccia di skinbot. Avvio: doppio click su applica_skin.bat (o `python skinbot_gui.py`)."""
import argparse
import concurrent.futures
import json
import queue
import random
import re
import sys
import threading
import time
import tkinter as tk
import urllib.request
from tkinter import colorchooser, messagebox

from PIL import Image, ImageDraw, ImageTk

import skinbot as sb
import updater
import wallbot as wb

BG, PANEL, ROW, ROW_SEL = "#14151c", "#1d1f2a", "#1d1f2a", "#2c3040"
TEXT, MUTED, OK, BAD = "#eef0f6", "#8b90a3", "#3ddc84", "#ff5c6c"
WARN = "#ffb347"
FONT, FONT_B, FONT_H = ("Segoe UI", 10), ("Segoe UI Semibold", 10), ("Segoe UI Semibold", 15)
LAYOUTS = [("Come adesso", None), ("Classico", "original"), ("Macchie", "v2"), ("Angoli", "v3"), ("Casuale", "random")]
NAMES = ("Base", "Colore A", "Colore B")
PREVIEW_W, PREVIEW_H = 520, 340


def hexcol(rgb):
    return "#" + wb.to_hex(rgb)


def readable_on(rgb):
    return "#101018" if (rgb[0] * 299 + rgb[1] * 587 + rgb[2] * 114) / 1000 > 150 else "#ffffff"


def srgb(lin):
    return tuple(round(255 * max(0.0, min(1.0, lin[k])) ** (1 / 2.2)) for k in "rgb")


class SkinPicker:
    """Griglia di icone delle skin di Fortnite (come FortnitePorting): scrivi per filtrare, clic per usarla."""
    TILE, GAP, COLS, PAGE = 84, 8, 8, 160

    def __init__(self, app, query=""):
        self.app = app
        self.outfits, self.shown, self.photos, self.items, self.tones = [], [], {}, {}, {}
        self.icons = queue.Queue()
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=8)
        self.asked = set()
        self.limit = self.PAGE
        self.after_id = None
        win = self.win = tk.Toplevel(app.root)
        win.title("Scegli la skin")
        win.configure(bg=BG)
        width = self.COLS * (self.TILE + self.GAP) + self.GAP + 40
        win.geometry(f"{width}x660+{app.root.winfo_rootx() + 40}+{app.root.winfo_rooty() + 60}")
        win.transient(app.root)
        win.protocol("WM_DELETE_WINDOW", self.close)

        top = tk.Frame(win, bg=BG)
        top.pack(fill="x", padx=12, pady=(12, 8))
        tk.Label(top, text="Cerca", font=FONT_B, bg=BG, fg=TEXT).pack(side="left", padx=(0, 8))
        self.query = tk.StringVar(value=query)
        entry = tk.Entry(top, textvariable=self.query, font=("Segoe UI", 12), bg=PANEL, fg=TEXT,
                         insertbackground=TEXT, relief="flat")
        entry.pack(side="left", fill="x", expand=True, ipady=7)
        entry.focus_set()
        entry.bind("<Return>", lambda e: self.pick(self.shown[0]) if self.shown else None)
        self.count = tk.Label(top, text="carico il catalogo...", font=FONT, bg=BG, fg=MUTED)
        self.count.pack(side="left", padx=(10, 0))
        self.query.trace_add("write", lambda *a: self.schedule())

        frame = tk.Frame(win, bg=BG)
        frame.pack(fill="both", expand=True, padx=(12, 4), pady=(0, 6))
        self.canvas = tk.Canvas(frame, bg=PANEL, highlightthickness=0)
        bar = tk.Scrollbar(frame, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.on_scroll(bar))
        bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"))
        self.canvas.bind("<Motion>", self.hover)
        self.canvas.bind("<Leave>", lambda e: self.tip.place_forget())
        self.canvas.bind("<Button-1>", self.click)
        self.tip = tk.Label(self.canvas, text="", font=FONT_B, bg="#151821", fg=TEXT, padx=8, pady=4, justify="left")
        self.status = tk.Label(win, text="Clic su una skin per usarla", font=FONT, bg=BG, fg=MUTED, anchor="w")
        self.status.pack(fill="x", padx=14, pady=(0, 8))

        def load():
            try:
                outfits = sb.load_outfits()
            except sb.BotError as exc:
                outfits = str(exc)
            self.icons.put(("outfits", outfits))
        threading.Thread(target=load, daemon=True).start()
        self.poll()

    def on_scroll(self, bar):
        def moved(first, last):
            bar.set(first, last)
            if float(last) > 0.97 and self.limit < len(self.matches()):   # in fondo: altre skin
                self.limit += self.PAGE
                self.draw(keep=True)
            self.request_visible()
        return moved

    def close(self):
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.win.destroy()

    def matches(self):
        return sb.filter_outfits(self.outfits, self.query.get())

    def schedule(self):
        if self.after_id:
            self.win.after_cancel(self.after_id)
        self.after_id = self.win.after(200, lambda: self.draw())

    def tile_at(self, i):
        step = self.TILE + self.GAP
        return self.GAP + (i % self.COLS) * step, self.GAP + (i // self.COLS) * step

    def draw(self, keep=False):
        if not keep:
            self.limit = self.PAGE
            self.canvas.yview_moveto(0)
        found = self.matches()
        self.shown = found[:self.limit]
        self.canvas.delete("all")
        self.items = {}
        for i, item in enumerate(self.shown):
            x, y = self.tile_at(i)
            top, bottom = (item["colors"] + [None, None])[:2] if item["colors"] else (None, None)
            light, dark = (top, bottom or top) if top else sb.RARITY_COLORS.get(item["rv"], ("#3a7bd5", "#1c3f7a"))
            self.tones[item["id"]] = (light, dark)
            half = self.TILE // 2
            self.canvas.create_rectangle(x, y, x + self.TILE, y + self.TILE, fill=dark, outline="")
            photo = self.photos.get(item["id"])
            img = self.canvas.create_image(x + half, y + half, image=photo) if photo else \
                self.canvas.create_image(x + half, y + half)
            self.canvas.create_rectangle(x, y, x + self.TILE, y + self.TILE, outline="", width=3, tags=f"sel{i}")
            self.items[item["id"]] = img
        rows = (len(self.shown) + self.COLS - 1) // self.COLS
        self.canvas.configure(scrollregion=(0, 0, self.COLS * (self.TILE + self.GAP) + self.GAP,
                                            rows * (self.TILE + self.GAP) + self.GAP))
        more = f" (prime {len(self.shown)}, scorri per le altre)" if len(found) > len(self.shown) else ""
        self.count.config(text=f"{len(found)} skin{more}")
        self.win.after(30, self.request_visible)

    def request_visible(self):
        """Scarica solo le icone che si vedono (piu' una riga sotto); quelle gia' scaricate restano sul PC."""
        if not self.shown:
            return
        step = self.TILE + self.GAP
        y0 = self.canvas.canvasy(0)
        y1 = y0 + self.canvas.winfo_height() + step
        first = max(0, int(y0 // step) * self.COLS)
        last = min(len(self.shown), int(y1 // step + 1) * self.COLS)
        for item in self.shown[first:last]:
            if item["id"] in self.asked:
                continue
            self.asked.add(item["id"])

            def work(item=item):
                try:
                    self.icons.put(("icon", item["id"], sb.outfit_icon(item)))
                except Exception:                  # icona mancante: resta il riquadro colorato
                    pass
            self.pool.submit(work)

    def poll(self):
        if not self.win.winfo_exists():
            return
        try:
            for _ in range(40):
                msg = self.icons.get_nowait()
                if msg[0] == "outfits":
                    if isinstance(msg[1], str):
                        self.count.config(text="")
                        self.status.config(text=msg[1], fg=BAD)
                    else:
                        self.outfits = msg[1]
                        self.draw()
                    continue
                _, oid, path = msg
                try:
                    icon = Image.open(path).convert("RGBA").resize((self.TILE, self.TILE), Image.LANCZOS)
                except OSError:
                    continue
                self.photos[oid] = ImageTk.PhotoImage(self.backdrop(oid, icon))
                if oid in self.items:
                    self.canvas.itemconfig(self.items[oid], image=self.photos[oid])
        except queue.Empty:
            pass
        self.win.after(60, self.poll)

    def backdrop(self, oid, icon):
        """Icona sul suo sfondo di rarita': chiaro in alto al centro, scuro in basso (come nel gioco)."""
        light, dark = (tuple(int(c[i:i + 2], 16) for i in (1, 3, 5)) for c in self.tones.get(oid, ("#3a7bd5", "#1c3f7a")))
        n = self.TILE
        mask = Image.radial_gradient("L").resize((n * 2, n * 2)).crop((n // 2, n // 4, n // 2 + n, n // 4 + n))
        bg = Image.composite(Image.new("RGB", (n, n), dark), Image.new("RGB", (n, n), light), mask).convert("RGBA")
        bg.alpha_composite(icon)
        return bg

    def index_at(self, event):
        step = self.TILE + self.GAP
        x, y = self.canvas.canvasx(event.x) - self.GAP, self.canvas.canvasy(event.y) - self.GAP
        if x < 0 or y < 0 or x % step > self.TILE or y % step > self.TILE:
            return None
        col, row = int(x // step), int(y // step)
        i = row * self.COLS + col
        return i if col < self.COLS and i < len(self.shown) else None

    def hover(self, event):
        i = self.index_at(event)
        self.canvas.itemconfig("all_sel", outline="")
        for tag in getattr(self, "_lit", []):
            self.canvas.itemconfig(tag, outline="")
        if i is None:
            self.tip.place_forget()
            return
        self.canvas.itemconfig(f"sel{i}", outline=TEXT)
        self._lit = [f"sel{i}"]
        item = self.shown[i]
        other = f"  ({item['name_en']})" if item["name_en"] and item["name_en"] != item["name"] else ""
        self.tip.config(text=f"{item['name']}{other}\n{item['rarity']}" + (f"  -  {item['set']}" if item["set"] else ""))
        x = min(event.x + 14, self.canvas.winfo_width() - self.tip.winfo_reqwidth() - 4)
        self.tip.place(x=max(4, x), y=event.y + 18)
        self.status.config(text=f"{item['name']}{other}   -   clic per usarla", fg=TEXT)

    def click(self, event):
        i = self.index_at(event)
        if i is not None:
            self.pick(self.shown[i])

    def pick(self, item):
        self.close()
        self.app.take_outfit(item)


class App:
    def __init__(self, root):
        self.root = root
        root.title("Skinbot")
        root.configure(bg=BG)
        root.minsize(940, 960)
        self.lines = queue.Queue()
        self.busy = False
        self.manual = None                         # palette scelta a mano, altrimenti automatica
        self.colors = list(sb.DEFAULT_PALETTE)
        self.layout = None
        self.palettes = {}                         # codice -> (colori, origine)
        self.rows = {}
        self.code = None
        self.watch_seen = sb.export_stamp()
        self.watch_pending = None
        self.uefn_ok = None
        self.preparing = set()                     # skin di cui si sta preparando la foto
        self.names = sb.load_names()               # codice -> nome vero della skin
        self.name_lbls = {}
        self.style_cache = {}                      # codice -> [(nome stile, colori)]
        self.styles = []
        self.style_idx = 0                         # -1 = colori scelti a mano
        self.pending_update = None                 # aggiornamento scaricato, da installare alla chiusura
        self.started = time.monotonic()
        self.checking_update = False

        file_log = sb.log
        def gui_log(*parts):                       # stesso log su file, piu' la finestra
            file_log(*parts)
            self.lines.put(" ".join(str(p) for p in parts))
        sb.log = gui_log

        self._build()
        self.refresh()
        self.fetch_names()
        self.root.after(150, self.pump)
        self.root.after(2000, self.watch_tick)
        self.check_uefn()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        if "--updated" in sys.argv:                # riaperta dall'aggiornamento appena installato
            version = sys.argv[sys.argv.index("--updated") + 1:][:1]
            self.show_banner(f"Skinbot aggiornato alla versione {version[0] if version else ''}: "
                             f"{(updater.read_json(updater.VERSION_FILE, {}) or {}).get('notes', '')}", OK)
            self.root.after(12000, self.hide_banner)
        self.root.after(1500, self.update_tick)

    # ------------------------------------------------------------ costruzione

    def _build(self):
        head = tk.Frame(self.root, bg=BG)
        head.pack(fill="x", padx=18, pady=(14, 8))
        tk.Label(head, text="Skinbot", font=("Segoe UI Semibold", 20), bg=BG, fg=TEXT).pack(side="left")
        tk.Label(head, text=f"v{updater.local_version()}" + ("  (sviluppo)" if updater.is_dev() else ""),
                 font=FONT, bg=BG, fg=MUTED).pack(side="left", padx=(8, 0), pady=(8, 0))
        tk.Label(head, text="skin, rampa e cielo abbinati", font=FONT, bg=BG, fg=MUTED).pack(side="left", padx=10, pady=(8, 0))
        self.uefn_lbl = tk.Label(head, text="●  UEFN", font=FONT_B, bg=BG, fg=MUTED)
        self.uefn_lbl.pack(side="right")

        # striscia degli avvisi (aggiornamenti): compare solo quando serve
        self.banner = tk.Frame(self.root, bg=PANEL)
        self.banner_lbl = tk.Label(self.banner, text="", font=FONT_B, bg=PANEL, fg=TEXT, anchor="w", padx=14, pady=8)
        self.banner_lbl.pack(side="left", fill="x", expand=True)
        self.banner_btn = tk.Label(self.banner, text="", font=FONT_B, bg=ROW_SEL, fg=TEXT, padx=14, pady=8, cursor="hand2")
        self.banner_cmd = None
        self.banner_btn.bind("<Button-1>", lambda e: self.banner_cmd and self.banner_cmd())

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=18, pady=(0, 16))
        self.body = body

        left = tk.Frame(body, bg=PANEL, width=250)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        tk.Label(left, text="Cerca una skin di Fortnite", font=FONT_B, bg=PANEL, fg=TEXT).pack(anchor="w", padx=12, pady=(12, 4))
        find = tk.Frame(left, bg=PANEL)
        find.pack(fill="x", padx=10, pady=(0, 8))
        self.search_var = tk.StringVar()
        entry = tk.Entry(find, textvariable=self.search_var, font=FONT, bg=ROW, fg=TEXT, insertbackground=TEXT,
                         relief="flat")
        entry.pack(side="left", fill="x", expand=True, ipady=5)
        entry.bind("<Return>", lambda e: self.search())
        self.search_btn = self._button(find, "Sfoglia", self.search, small=True)
        self.search_btn.pack(side="left", padx=(6, 0))
        tk.Label(left, text="Le tue skin", font=FONT_B, bg=PANEL, fg=TEXT).pack(anchor="w", padx=12, pady=(4, 6))
        # i tasti in fondo si impacchettano prima dell'elenco: cosi' restano visibili anche con tante skin
        clean = self._button(left, "Tieni solo l'ultima skin", self.clean_all, small=True)
        clean.config(fg="#ff8a80")
        clean.pack(side="bottom", fill="x", padx=10, pady=(0, 10))
        self._button(left, "Prepara questa mappa", self.setup_map, small=True).pack(side="bottom", fill="x", padx=10, pady=(0, 6))
        refs = self._button(left, "Prepara mappa + Riferimenti giocatore", lambda: self.setup_map(player_refs=True), small=True)
        refs.config(fg=OK)
        refs.pack(side="bottom", fill="x", padx=10, pady=(0, 6))
        self._button(left, "Aggiorna elenco", self.refresh, small=True).pack(side="bottom", fill="x", padx=10, pady=(0, 6))
        self._button(left, "Storico delle skin fatte", self.show_history, small=True).pack(side="bottom", fill="x", padx=10, pady=(0, 6))
        self._button(left, "Skin uscita male? Diagnosi", self.diagnose, small=True).pack(side="bottom", fill="x", padx=10, pady=6)
        # elenco scorrevole con la rotella
        self.list_canvas = tk.Canvas(left, bg=PANEL, highlightthickness=0)
        self.list_canvas.pack(fill="both", expand=True, padx=6)
        self.list_box = tk.Frame(self.list_canvas, bg=PANEL)
        window = self.list_canvas.create_window((0, 0), window=self.list_box, anchor="nw")
        self.list_box.bind("<Configure>", lambda e: self.list_canvas.config(scrollregion=self.list_canvas.bbox("all")))
        self.list_canvas.bind("<Configure>", lambda e: self.list_canvas.itemconfig(window, width=e.width))

        def wheel(event):
            over = self.root.winfo_containing(event.x_root, event.y_root)
            if over is not None and str(over).startswith(str(self.list_canvas)) \
                    and self.list_box.winfo_height() > self.list_canvas.winfo_height():
                self.list_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        self.root.bind_all("<MouseWheel>", wheel)

        right = tk.Frame(body, bg=BG)
        right.pack(side="left", fill="both", expand=True, padx=(16, 0))

        self.title = tk.Label(right, text="", font=FONT_H, bg=BG, fg=TEXT)
        self.title.pack(anchor="w")
        self.origin = tk.Label(right, text="", font=FONT, bg=BG, fg=MUTED)
        self.origin.pack(anchor="w", pady=(0, 8))

        self.preview = tk.Label(right, bd=0, bg=BG)
        self.preview.pack(anchor="w")

        chips = tk.Frame(right, bg=BG)
        chips.pack(anchor="w", pady=(12, 4))
        self.chips = []
        for i, name in enumerate(NAMES):
            chip = tk.Label(chips, text=name, font=FONT_B, width=14, height=2, cursor="hand2", bd=0)
            chip.pack(side="left", padx=(0, 8))
            chip.bind("<Button-1>", lambda e, i=i: self.pick(i))
            self.chips.append(chip)
        self.auto_btn = self._button(chips, "Colori della skin", self.auto_colors, small=True)
        self.auto_btn.pack(side="left", padx=(6, 0))
        # solo per le skin che il gioco tinge (pelo grigio nell'export): il colore lo scegli qui
        self.dye_btn = tk.Label(chips, text="Colore della skin", font=FONT_B, padx=12, pady=6, cursor="hand2")
        self.dye_btn.bind("<Button-1>", lambda e: self.pick_dye())
        # skin con piu' varianti di colore esportate (es. Ghoul Trooper verde e rosa): un clic passa alla successiva
        self.variant_btn = tk.Label(chips, text="", font=FONT_B, padx=12, pady=6, cursor="hand2", bg=ROW_SEL, fg=TEXT)
        self.variant_btn.bind("<Button-1>", lambda e: self.next_variant())

        tk.Label(right, text="STILE DEI COLORI  -  stessa skin, video diverso: clicca per provarlo nell'anteprima",
                 font=("Segoe UI", 9), bg=BG, fg=MUTED).pack(anchor="w", pady=(10, 4))
        self.styles_box = tk.Frame(right, bg=BG)
        self.styles_box.pack(anchor="w")

        lay = tk.Frame(right, bg=BG)
        lay.pack(anchor="w", pady=(10, 4))
        tk.Label(lay, text="Disegno esagoni", font=FONT, bg=BG, fg=MUTED).pack(side="left", padx=(0, 10))
        self.layout_btns = []
        for label, value in LAYOUTS:
            btn = tk.Label(lay, text=label, font=FONT, padx=12, pady=5, cursor="hand2", bg=PANEL, fg=TEXT)
            btn.pack(side="left", padx=(0, 4))
            btn.bind("<Button-1>", lambda e, v=value: self.set_layout(v))
            self.layout_btns.append((btn, value))

        act = tk.Frame(right, bg=BG)
        act.pack(fill="x", pady=(12, 8))
        self.apply_btn = tk.Label(act, text="APPLICA A UEFN", font=("Segoe UI Semibold", 13), padx=26, pady=11, cursor="hand2")
        self.apply_btn.pack(side="left")
        self.apply_btn.bind("<Button-1>", lambda e: self.apply())
        self.auto_on = False
        self.auto_lbl = tk.Label(act, text="", font=FONT_B, padx=14, pady=11, cursor="hand2", bg=PANEL)
        self.auto_lbl.pack(side="left", padx=10)
        self.auto_lbl.bind("<Button-1>", lambda e: self.toggle_auto())
        self.statue_mode = sb.statue_mode()       # modello / giocatore / off
        self.statues_lbl = tk.Label(act, text="", font=FONT_B, padx=14, pady=11, cursor="hand2", bg=PANEL)
        self.statues_lbl.pack(side="left")
        self.statues_lbl.bind("<Button-1>", lambda e: self.toggle_statues())
        self.falls_on = self.load_settings().get("cascate")      # None finche' non lo si sceglie: non tocca nulla
        self.falls_lbl = tk.Label(act, text="", font=FONT_B, padx=14, pady=11, cursor="hand2", bg=PANEL)
        self.falls_lbl.pack(side="left", padx=10)
        self.falls_lbl.bind("<Button-1>", lambda e: self.toggle_falls())

        self.text = tk.Text(right, height=7, state="disabled", wrap="word", bg=PANEL, fg=TEXT, bd=0,
                            font=("Consolas", 9), padx=10, pady=8, insertbackground=TEXT)
        self.text.pack(fill="both", expand=True)
        self.text.tag_config("err", foreground=BAD)
        self.text.tag_config("ok", foreground=OK)
        self._toggles()
        self._layout_buttons()

    def _button(self, parent, text, command, small=False):
        btn = tk.Label(parent, text=text, font=FONT if small else FONT_B, bg=ROW_SEL, fg=TEXT,
                       padx=12, pady=6, cursor="hand2")
        btn.bind("<Button-1>", lambda e: command())
        return btn

    # ------------------------------------------------------------ elenco

    def refresh(self):
        # i pezzi sciolti (braccia, teste, accessori senza corpo) non sono skin: fuori dall'elenco
        self.skins = {c: f for c, f in sb.exported_skins().items() if sb.has_body(f)}
        self.catalog = {c: i for c, i in sb.catalog_skins().items() if c not in self.skins}
        newest = sb.latest_code(self.skins)
        history = sb.load_history()
        for child in self.list_box.winfo_children():
            child.destroy()
        self.rows = {}
        self.name_lbls = {}
        if not self.skins and not self.catalog:
            tk.Label(self.list_box, text="Nessuna skin.\nCercala qui sopra, oppure\nesportala da FortnitePorting.", font=FONT,
                     bg=PANEL, fg=MUTED, justify="left").pack(anchor="w", padx=8, pady=8)
        everything = sorted(self.skins, key=lambda c: c.lower()) + sorted(self.catalog, key=lambda c: c.lower())
        for code in everything:
            row = tk.Frame(self.list_box, bg=ROW, cursor="hand2")
            row.pack(fill="x", pady=1)
            dots = tk.Canvas(row, width=46, height=30, bg=ROW, highlightthickness=0)
            dots.pack(side="left", padx=(8, 4))
            complete = code in self.catalog or sb.has_body(self.skins[code])
            name = tk.Label(row, text=sb.display_name(code, self.names), font=FONT_B if complete else FONT, bg=ROW,
                            fg=TEXT if complete else MUTED, anchor="w")
            name.pack(side="left", fill="x", expand=True)
            self.name_lbls[code] = name
            done = len(history.get(code, {}).get("usi", []))
            if done:
                tk.Label(row, text=f"fatta x{done}", font=("Segoe UI", 8), bg=ROW, fg=WARN).pack(side="right", padx=8)
            elif code == newest:
                tk.Label(row, text="nuova", font=("Segoe UI", 8), bg=ROW, fg=OK).pack(side="right", padx=8)
            elif code in self.catalog:
                tk.Label(row, text="catalogo", font=("Segoe UI", 8), bg=ROW, fg=MUTED).pack(side="right", padx=8)
            elif not complete:
                tk.Label(row, text="senza corpo", font=("Segoe UI", 8), bg=ROW, fg=MUTED).pack(side="right", padx=8)
            for widget in (row, dots, name):
                widget.bind("<Button-1>", lambda e, c=code: self.select(c))
            self.rows[code] = (row, dots)
            self.draw_dots(code)
        self.select(newest if newest in self.skins else next(iter(everything), None))

    def draw_dots(self, code):
        if code in self.rows:
            dots = self.rows[code][1]
            dots.delete("all")
            for i, col in enumerate(self.palette_of(code)[0]):
                dots.create_oval(2 + i * 14, 8, 16 + i * 14, 22, fill=hexcol(col), outline="")

    def preview_path(self, code):
        return sb.HERE / "skins" / code / "preview.png"

    def prepare(self, code):
        """Senza toccare UEFN: converte la skin per avere foto e colori misurati."""
        if not code or code in self.preparing or self.busy or code not in self.skins:
            return                                 # le skin del catalogo hanno gia' foto e colori
        # modelli riletti adesso: l'elenco puo' essere stato fatto mentre FortnitePorting stava ancora esportando
        models, parts = sb.exported_skins().get(code) or self.skins[code], sb.part_textures(code)
        self.skins[code] = models
        if self.preview_path(code).is_file() and not sb.preview_stale(code, parts, models):
            return
        if not sb.export_settled(models, parts):   # export ancora in corso: riprovo tra poco
            self.root.after(2500, lambda: self.prepare(code) if self.code == code else None)
            return
        self.preparing.add(code)

        def work():
            try:
                sb.convert(code, models, parts)
            except Exception:
                pass                               # resta l'anteprima senza foto
            finally:
                self.lines.put(("prepared", code))
        threading.Thread(target=work, daemon=True).start()

    def search(self):
        """Apre la griglia delle skin di Fortnite, gia' filtrata con quello che hai scritto."""
        picker = getattr(self, "picker", None)
        if picker and picker.win.winfo_exists():
            picker.query.set(self.search_var.get())
            picker.win.lift()
            return
        self.picker = SkinPicker(self, self.search_var.get())

    def take_outfit(self, item):
        sb.log(f"Prendo {item['name']} dal catalogo...")

        def work():
            try:
                self.lines.put(("added", sb.add_catalog_skin(item)))
            except sb.BotError as exc:
                sb.log("ERRORE:", exc)
        threading.Thread(target=work, daemon=True).start()

    def fetch_names(self):
        codes = [c for c in self.skins if c not in self.names]
        if not codes:
            return

        def work():
            sb.resolve_names(codes)
            self.lines.put(("names", None))
        threading.Thread(target=work, daemon=True).start()

    def parts_of(self, code):
        """Texture dell'export; le skin del catalogo non ne hanno (colori dalla foto ufficiale)."""
        return sb.part_textures(code) if code in self.skins else {}

    def palette_of(self, code):
        if code not in self.palettes:
            self.palettes[code] = sb.choose_palette(code, self.parts_of(code), None)
        return self.palettes[code]

    def select(self, code):
        self.code = code
        for c, (row, dots) in self.rows.items():
            colour = ROW_SEL if c == code else ROW
            row.config(bg=colour)
            for child in row.winfo_children():
                child.config(bg=colour)
        self.title.config(text=sb.display_name(code, self.names) if code else "Nessuna skin esportata")
        self.prepare(code)
        self.auto_colors()                         # skin nuova: si riparte dallo stile automatico
        self.pick_style(self.fresh_style())        # ...o dal primo stile non ancora usato per questa skin
        self.show_dye()
        self.show_variant()

    def fresh_style(self):
        """L'indice del primo stile mai usato per questa skin; se li ha gia' usati tutti, quello usato da piu' tempo."""
        used = sb.used_styles(self.code) if self.code else {}
        names = [name for name, _ in self.styles]
        for i, name in enumerate(names):
            if name not in used:
                return i
        return min(range(len(names)), key=lambda i: used[names[i]][-1])

    def show_variant(self):
        variants = sb.skin_variants(self.code) if self.code in self.skins else {}
        if len(variants) > 1:
            name = sb.default_variant(self.code, variants)
            nice = re.sub(r"^[MF]_(MED|LRG|SML)_", "", name).replace("_", " ")
            self.variant_btn.config(text=f"Variante: {nice}  ({sorted(variants).index(name) + 1}/{len(variants)})")
            self.variant_btn.pack(side="left", padx=(8, 0))
        else:
            self.variant_btn.pack_forget()

    def next_variant(self):
        """Passa alla variante di colore successiva della skin e rifa' la foto."""
        if self.busy or not self.code or self.code in self.preparing:
            return
        variants = sb.skin_variants(self.code)
        if len(variants) < 2:
            return
        names = sorted(variants)
        now = sb.default_variant(self.code, variants)
        sb.set_variant(self.code, names[(names.index(now) + 1) % len(names)])
        self.palettes.pop(self.code, None)
        self.style_cache.pop(self.code, None)
        self.show_variant()
        self.prepare(self.code)
        self.auto_colors()

    def show_dye(self):
        if self.code in self.skins and sb.can_dye(self.code):
            dye = sb.skin_dye(self.code)
            if dye:
                self.dye_btn.config(text="Colore della skin", bg=hexcol(dye), fg=readable_on(dye))
            else:                                  # nessuna tinta: la skin e' come esportata
                self.dye_btn.config(text="Colora la skin...", bg=ROW_SEL, fg=TEXT)
            self.dye_btn.pack(side="left", padx=(8, 0))
        else:
            self.dye_btn.pack_forget()

    def pick_dye(self):
        """Sceglie (o toglie) il colore di una skin che il gioco tinge, e rifa' la foto."""
        if self.busy or not self.code or self.code in self.preparing:
            return
        current = sb.skin_dye(self.code)
        if current:
            answer = messagebox.askyesnocancel("Colore della skin", "Vuoi cambiare il colore?\n\n"
                                               "Si' = scegli un altro colore\nNo = togli il colore (skin come esportata)")
            if answer is None:
                return
            if answer is False:
                sb.set_skin_dye(self.code, None)
        if not current or answer:
            rgb, _ = colorchooser.askcolor(color=hexcol(current or sb.DEFAULT_DYE), title="Colore della skin")
            if not rgb:
                return
            sb.set_skin_dye(self.code, rgb)
        self.palettes.pop(self.code, None)
        self.style_cache.pop(self.code, None)
        self.show_dye()
        self.prepare(self.code)
        self.origin.config(text=self.describe())

    # ------------------------------------------------------------ colori e anteprima

    def styles_of(self, code):
        if code not in self.style_cache:
            self.style_cache[code] = sb.palette_styles(code, self.parts_of(code))
        return self.style_cache[code]

    def auto_colors(self):
        self.styles = self.styles_of(self.code) if self.code else [("Automatico", list(sb.DEFAULT_PALETTE))]
        self.build_styles()
        self.pick_style(0)

    def describe(self):
        if not self.code:
            return "Esporta una skin da FortnitePorting su Assets Folder."
        note = "   Preparo la foto della skin..." if self.code in self.preparing else ""
        style = "colori scelti a mano" if self.style_idx < 0 else f"stile {self.styles[self.style_idx][0]}"
        used = sb.used_styles(self.code)
        done = sum(len(v) for v in used.values())
        past = f"   -   gia' fatta {done} volte: {', '.join(used)}" if done else "   -   mai fatta"
        return f"{self.code}   -   {style}{past}{note}"

    def pick_style(self, i):
        self.style_idx = i
        name, cols = self.styles[i]
        self.colors = list(cols)
        self.manual = None if i == 0 else list(cols)       # lo stile automatico lo ricalcola il bot
        self.origin.config(text=self.describe())
        self.paint()

    def build_styles(self):
        for child in self.styles_box.winfo_children():
            child.destroy()
        self.style_cards = []
        used = sb.used_styles(self.code) if self.code else {}
        for i, (name, cols) in enumerate(self.styles):
            card = tk.Frame(self.styles_box, bg=PANEL, cursor="hand2", highlightthickness=2, highlightbackground=BG)
            card.grid(row=i // 4, column=i % 4, padx=(0, 6), pady=(0, 6))
            sw = tk.Canvas(card, width=118, height=30, bg=PANEL, highlightthickness=0)
            sw.pack(padx=5, pady=(5, 2))
            sw.create_rectangle(0, 0, 70, 30, fill=hexcol(cols[0]), outline="")      # fondo: il colore piu' grande
            sw.create_rectangle(70, 0, 94, 30, fill=hexcol(cols[1]), outline="")
            sw.create_rectangle(94, 0, 118, 30, fill=hexcol(cols[2]), outline="")
            lbl = tk.Label(card, text=name, font=FONT, bg=PANEL, fg=TEXT)
            lbl.pack()
            dates = used.get(name)
            tag = tk.Label(card, text=f"usato {dates[-1][8:10]}/{dates[-1][5:7]}" + (f" x{len(dates)}" if len(dates) > 1 else "")
                           if dates else "libero", font=("Segoe UI", 8), bg=PANEL, fg=WARN if dates else OK)
            tag.pack(pady=(0, 4))
            for widget in (card, sw, lbl, tag):
                widget.bind("<Button-1>", lambda e, i=i: self.pick_style(i))
            self.style_cards.append(card)

    def mark_style(self):
        for i, card in enumerate(getattr(self, "style_cards", [])):
            card.config(highlightbackground=TEXT if i == self.style_idx else BG)

    def pick(self, i):
        rgb, _ = colorchooser.askcolor(hexcol(self.colors[i]), title=NAMES[i], parent=self.root)
        if rgb:
            self.colors[i] = tuple(int(v) for v in rgb)
            self.manual = list(self.colors)
            self.style_idx = -1
            self.origin.config(text=self.describe())
            self.paint()

    def set_layout(self, value):
        self.layout = value
        self._layout_buttons()
        self.paint()

    def _layout_buttons(self):
        for btn, value in self.layout_btns:
            on = value == self.layout
            btn.config(bg=hexcol(self.colors[0]) if on else PANEL, fg=readable_on(self.colors[0]) if on else TEXT)

    def paint(self):
        for chip, col, name in zip(self.chips, self.colors, NAMES):
            chip.config(bg=hexcol(col), fg=readable_on(col), text=f"{name}\n{wb.to_hex(col)}")
        base = self.colors[0]
        if not self.busy:
            self.apply_btn.config(bg=hexcol(base), fg=readable_on(base), text="APPLICA A UEFN")
        self._layout_buttons()
        self._toggles()
        self.mark_style()
        self.photo = ImageTk.PhotoImage(self.scene())
        self.preview.config(image=self.photo)

    def scene(self):
        """Anteprima: cielo tinto come in UEFN e una fila di pannelli con i colori scelti."""
        sky = srgb(sb.sky_tint(sb.sky_source(self.colors)))
        img = Image.new("RGB", (PREVIEW_W, PREVIEW_H))
        draw = ImageDraw.Draw(img)
        for y in range(PREVIEW_H):
            t = y / PREVIEW_H
            draw.line([(0, y), (PREVIEW_W, y)],          # piu' chiaro verso l'orizzonte
                      fill=tuple(min(255, round(c * (0.6 + 0.4 * t) + 70 * t * t)) for c in sky))
        try:
            cells = wb.load_layout(self.layout or "original")
        except SystemExit:
            cells = {}
        if self.layout == "random":
            cells = wb.random_layout(random.Random(7))
        tile = wb.render_wall(170, cells, self.colors, wb.parse_hex(wb.LINE_COLOR), None)
        y = PREVIEW_H - 170 - 18
        for i in range(3):
            x = 10 + i * 168
            img.paste(tile, (x, y), tile)
        photo = self.preview_path(self.code) if self.code else None
        if photo and photo.is_file():
            try:
                skin = Image.open(photo).convert("RGBA")
                h = PREVIEW_H - 8
                skin = skin.resize((round(skin.width * h / skin.height), h), Image.LANCZOS)
                img.paste(skin, ((PREVIEW_W - skin.width) // 2, 4), skin)
            except OSError:
                pass
        return img

    # ------------------------------------------------------------ interruttori

    def _toggles(self):
        accent = hexcol(self.colors[0])
        self.auto_lbl.config(text=("●  Automatico: ON" if self.auto_on else "○  Automatico: OFF"),
                             fg=accent if self.auto_on else MUTED)
        self.statues_lbl.config(text={"modello": "●  Statue: Modello", "giocatore": "●  Statue: Giocatore",
                                      "off": "○  Statue: OFF"}[self.statue_mode],
                                fg=MUTED if self.statue_mode == "off" else accent)
        self.falls_lbl.config(text=("●  Cascate XP: ON" if self.falls_on else "○  Cascate XP: OFF"),
                              fg=accent if self.falls_on else MUTED)

    def load_settings(self):
        try:
            return json.loads((sb.HERE / "settings.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def toggle_falls(self):
        self.falls_on = not self.falls_on
        try:
            (sb.HERE / "settings.json").write_text(json.dumps({**self.load_settings(), "cascate": self.falls_on}),
                                                   encoding="utf-8")
        except OSError:
            pass
        self._toggles()
        sb.log("Cascate XP: le metto al prossimo APPLICA A UEFN." if self.falls_on
               else "Cascate XP: le tolgo al prossimo APPLICA A UEFN.")

    def toggle_statues(self):
        order = ["modello", "giocatore", "off"]
        self.statue_mode = order[(order.index(self.statue_mode) + 1) % len(order)]
        try:
            (sb.HERE / "settings.json").write_text(json.dumps({**self.load_settings(), "statue": self.statue_mode}),
                                                   encoding="utf-8")
        except OSError:
            pass
        self._toggles()
        sb.log({"modello": "Statue: la skin convertita dall'export (anche skin che non hai).",
                "giocatore": "Statue: Riferimento giocatore. In partita mostrano la skin che indossi, "
                             "sempre perfetta e senza conversione. Si vedono solo in sessione, non nell'editor.",
                "off": "Statue: non le tocco."}[self.statue_mode])

    def toggle_auto(self):
        self.auto_on = not self.auto_on
        self.watch_seen = sb.export_stamp()
        self.watch_pending = None
        self._toggles()
        if self.auto_on:
            sb.log("Automatico attivo: esporta una skin da FortnitePorting su Assets Folder.")

    # ------------------------------------------------------------ esecuzione

    def apply(self, code=None):
        code = code or self.code
        if self.busy or not code:
            return
        if code in self.preparing:
            sb.log("Sto ancora preparando la foto di questa skin: riprova tra qualche secondo.")
            return
        self.busy = True
        self.apply_btn.config(text="STO LAVORANDO...", bg=ROW_SEL, fg=MUTED)
        style = "A mano" if self.style_idx < 0 else self.styles[self.style_idx][0]
        args = argparse.Namespace(colors=self.manual, layout=self.layout, style=style,
                                  no_statues=self.statue_mode == "off", statue=self.statue_mode,
                                  cascate=self.falls_on, dry_run=False)

        def work():
            try:
                sb.safe_run(code, args)
            finally:
                self.lines.put(None)               # segnale di fine
        threading.Thread(target=work, daemon=True).start()

    def setup_map(self, player_refs=False):
        """Una volta sola per progetto: crea i materiali del bot e collega muri e cielo.

        Con player_refs mette anche le tre statue Riferimento giocatore (e le statue passano su "Giocatore").
        """
        if self.busy:
            return
        self.busy = True
        self.apply_btn.config(text="PREPARO LA MAPPA...", bg=ROW_SEL, fg=MUTED)

        def work():
            try:
                if sb.safe_setup(player_refs=player_refs) and player_refs:
                    self.statue_mode = "giocatore"
            finally:
                self.lines.put(None)
        threading.Thread(target=work, daemon=True).start()

    def diagnose(self):
        """Scrive sul Desktop un file con i dettagli della skin selezionata, da mandare a chi sviluppa il bot."""
        if not self.code:
            return
        try:
            out = sb.diagnose(self.code)
            sb.log(f"Diagnosi scritta: {out}")
            sb.log("  Manda questo file insieme a uno screenshot della skin uscita male.")
        except Exception as exc:
            sb.log("ERRORE nella diagnosi:", f"{type(exc).__name__}: {str(exc)[:200]}")

    def show_history(self):
        sb.log("STORICO DELLE SKIN FATTE")
        for line in sb.history_lines():
            sb.log("  " + line)

    def clean_all(self):
        """Tiene solo l'ultima skin esportata e cancella le altre da UEFN e dal PC, dopo una conferma."""
        if self.busy or self.preparing:
            sb.log("Sto ancora lavorando: riprova tra qualche secondo.")
            return
        code, others, files, mb = sb.clean_plan()
        if not code:
            sb.log("Non c'e' nessuna skin esportata: niente da pulire.")
            return
        if not messagebox.askyesno("Tieni solo l'ultima skin", (
                f"Tengo solo l'ultima skin esportata:\n\n      {sb.display_name(code, self.names)}\n\n"
                f"e cancello tutte le altre:\n\n"
                f"  -  dal PC: {others} skin vecchie e i pezzi sciolti ({files} file, {mb} MB)\n"
                f"  -  dal progetto UEFN: le skin vecchie importate\n\n"
                f"Resta anche la skin che ora e' sulle statue. Muri, cielo e cascate non cambiano.\n"
                f"Non si puo' annullare.\n\nContinuo?"), icon="warning", default="no"):
            return
        self.busy = True
        self.apply_btn.config(text="PULISCO...", bg=ROW_SEL, fg=MUTED)

        def work():
            try:
                sb.safe_clean()
            finally:
                self.lines.put(("cleaned", None))
                self.lines.put(None)
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------ aggiornamenti

    def show_banner(self, text, color=TEXT, button=None, command=None):
        self.banner_lbl.config(text=text, fg=color)
        self.banner_cmd = command
        if button:
            self.banner_btn.config(text=button)
            self.banner_btn.pack(side="right", padx=8, pady=4)
        else:
            self.banner_btn.pack_forget()
        self.banner.pack(fill="x", padx=18, pady=(0, 10), before=self.body)

    def hide_banner(self):
        self.banner.pack_forget()

    def update_tick(self):
        """Controlla il canale: all'avvio installa subito, piu' tardi aspetta la chiusura."""
        if updater.is_dev() or not updater.channel_url():
            return
        if not self.checking_update and self.pending_update is None:
            self.checking_update = True

            def work():
                try:
                    update = updater.check()
                    if update:
                        updater.download(update)
                        self.lines.put(("update_ready", update))
                except Exception as exc:
                    updater.log("download fallito:", f"{type(exc).__name__}: {str(exc)[:160]}")
                finally:
                    self.checking_update = False
            threading.Thread(target=work, daemon=True).start()
        self.root.after(30 * 60 * 1000, self.update_tick)

    def update_ready(self, update):
        at_startup = time.monotonic() - self.started < 90 and not self.busy
        if at_startup:
            self.show_banner(f"Installo la versione {update.version} e riavvio Skinbot...", OK)
            self.root.after(400, lambda: self.restart_now(update))
            return
        self.pending_update = update
        self.show_banner(f"Versione {update.version} scaricata ({update.notes or 'aggiornamento'}): "
                         "si installa quando chiudi Skinbot.", OK, "Riavvia ora", lambda: self.restart_now(update))

    def restart_now(self, update):
        if self.busy:
            sb.log("Aggiornamento rimandato: aspetto che finisca il lavoro in corso.")
            self.pending_update = update
            return
        try:
            version = updater.apply()
        except Exception as exc:
            self.pending_update = None
            self.show_banner(f"Aggiornamento non riuscito: {str(exc)[:120]}", BAD)
            return
        updater.relaunch(version)
        self.root.destroy()

    def on_close(self):
        if not self.busy and updater.ready():
            try:
                updater.apply()
            except Exception as exc:
                updater.log("installazione alla chiusura fallita:", str(exc)[:160])
        self.root.destroy()

    def refresh_styles(self):
        """Ricalcola gli stili (colori misurati appena arrivati) tenendo la scelta dell'utente."""
        if not self.code:
            return
        keep = self.style_idx
        if keep < 0:                               # colori a mano: restano quelli
            self.styles = self.styles_of(self.code)
            self.build_styles()
            self.origin.config(text=self.describe())
            self.paint()
            return
        self.styles = self.styles_of(self.code)
        self.build_styles()
        self.pick_style(keep if keep < len(self.styles) else 0)

    def pump(self):
        try:
            while True:
                line = self.lines.get_nowait()
                if isinstance(line, tuple) and line[0] == "update_ready":
                    self.update_ready(line[1])
                    continue
                if isinstance(line, tuple) and line[0] == "cleaned":
                    self.palettes.clear()
                    self.style_cache.clear()
                    self.manual = None
                    self.code = None
                    self.refresh()
                    continue
                if isinstance(line, tuple) and line[0] == "added":
                    code = line[1]
                    self.palettes.pop(code, None)
                    self.style_cache.pop(code, None)
                    self.names = sb.load_names()
                    self.refresh()
                    self.select(code)
                    self.list_canvas.update_idletasks()
                    rows = list(self.rows)
                    if code in rows:                # la riga nuova sta in fondo: la porto in vista
                        self.list_canvas.yview_moveto(rows.index(code) / max(1, len(rows)))
                    sb.log(f"{sb.display_name(code)}: colori presi dalla foto ufficiale. "
                           "Premi APPLICA A UEFN (le statue saranno Riferimento giocatore).")
                    continue
                if isinstance(line, tuple) and line[0] == "names":
                    self.names = sb.load_names()
                    for code, lbl in self.name_lbls.items():
                        lbl.config(text=sb.display_name(code, self.names))
                    if self.code:
                        self.title.config(text=sb.display_name(self.code, self.names))
                    continue
                if isinstance(line, tuple):        # ("prepared", codice): foto e colori pronti
                    code = line[1]
                    self.preparing.discard(code)
                    self.palettes.pop(code, None)
                    self.style_cache.pop(code, None)
                    self.draw_dots(code)
                    if self.code == code:
                        self.refresh_styles()
                    continue
                if line is None:
                    self.busy = False
                    self.palettes.pop(self.code, None)   # ora c'e' la misura fatta dal convertitore
                    self.style_cache.pop(self.code, None)
                    self.refresh_styles()
                    continue
                tag = "err" if "ERRORE" in line else ("ok" if line.startswith("Fatto") else "")
                self.text.config(state="normal")
                self.text.insert("end", line + "\n", tag)
                self.text.see("end")
                self.text.config(state="disabled")
        except queue.Empty:
            pass
        self.root.after(150, self.pump)

    def watch_tick(self):
        try:
            if self.auto_on and not self.busy:
                stamp = sb.export_stamp()
                if stamp > self.watch_seen:
                    if self.watch_pending == stamp:        # fermo da un giro: l'export e' finito
                        self.watch_seen, self.watch_pending = stamp, None
                        sb.log("Nuovo export rilevato.")
                        self.manual = None
                        self.palettes.clear()
                        self.style_cache.clear()
                        self.refresh()
                        self.fetch_names()
                        self.apply(sb.latest_code(self.skins))
                    else:
                        self.watch_pending = stamp
        finally:
            self.root.after(4000, self.watch_tick)

    def check_uefn(self):
        def probe():
            try:
                urllib.request.urlopen(urllib.request.Request(sb.MCP_URL, b"{}", {"Content-Type": "application/json"}), timeout=3)
                ok = True
            except urllib.error.HTTPError:
                ok = True                          # il server risponde, anche se rifiuta la richiesta vuota
            except OSError:
                ok = False
            self.uefn_ok = ok
        threading.Thread(target=probe, daemon=True).start()
        if self.uefn_ok is not None:
            self.uefn_lbl.config(text="●  UEFN collegato" if self.uefn_ok else "●  UEFN chiuso",
                                 fg=OK if self.uefn_ok else BAD)
        self.root.after(3000, self.check_uefn)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
