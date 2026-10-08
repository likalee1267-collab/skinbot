"""Interfaccia di skinbot. Avvio: doppio click su applica_skin.bat (o `python skinbot_gui.py`)."""
import argparse
import queue
import random
import sys
import threading
import time
import tkinter as tk
import urllib.request
from tkinter import colorchooser

from PIL import Image, ImageDraw, ImageTk

import skinbot as sb
import updater
import wallbot as wb

BG, PANEL, ROW, ROW_SEL = "#14151c", "#1d1f2a", "#1d1f2a", "#2c3040"
TEXT, MUTED, OK, BAD = "#eef0f6", "#8b90a3", "#3ddc84", "#ff5c6c"
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


class App:
    def __init__(self, root):
        self.root = root
        root.title("Skinbot")
        root.configure(bg=BG)
        root.minsize(940, 800)
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
        tk.Label(left, text="Skin esportate", font=FONT_B, bg=PANEL, fg=TEXT).pack(anchor="w", padx=12, pady=(12, 6))
        self.list_box = tk.Frame(left, bg=PANEL)
        self.list_box.pack(fill="both", expand=True, padx=6)
        self._button(left, "Prepara questa mappa", self.setup_map, small=True).pack(side="bottom", fill="x", padx=10, pady=(0, 10))
        self._button(left, "Aggiorna elenco", self.refresh, small=True).pack(side="bottom", fill="x", padx=10, pady=6)

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
        self.statues_on = True
        self.statues_lbl = tk.Label(act, text="", font=FONT_B, padx=14, pady=11, cursor="hand2", bg=PANEL)
        self.statues_lbl.pack(side="left")
        self.statues_lbl.bind("<Button-1>", lambda e: self.toggle_statues())

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
        self.skins = sb.exported_skins()
        newest = sb.latest_code(self.skins)
        for child in self.list_box.winfo_children():
            child.destroy()
        self.rows = {}
        self.name_lbls = {}
        if not self.skins:
            tk.Label(self.list_box, text="Nessuna skin.\nEsporta da FortnitePorting\nsu Assets Folder.", font=FONT,
                     bg=PANEL, fg=MUTED, justify="left").pack(anchor="w", padx=8, pady=8)
        for code in sorted(self.skins, key=lambda c: (not sb.has_body(self.skins[c]), c.lower())):
            row = tk.Frame(self.list_box, bg=ROW, cursor="hand2")
            row.pack(fill="x", pady=1)
            dots = tk.Canvas(row, width=46, height=30, bg=ROW, highlightthickness=0)
            dots.pack(side="left", padx=(8, 4))
            complete = sb.has_body(self.skins[code])
            name = tk.Label(row, text=sb.display_name(code, self.names), font=FONT_B if complete else FONT, bg=ROW,
                            fg=TEXT if complete else MUTED, anchor="w")
            name.pack(side="left", fill="x", expand=True)
            self.name_lbls[code] = name
            if code == newest:
                tk.Label(row, text="nuova", font=("Segoe UI", 8), bg=ROW, fg=OK).pack(side="right", padx=8)
            elif not complete:
                tk.Label(row, text="senza corpo", font=("Segoe UI", 8), bg=ROW, fg=MUTED).pack(side="right", padx=8)
            for widget in (row, dots, name):
                widget.bind("<Button-1>", lambda e, c=code: self.select(c))
            self.rows[code] = (row, dots)
            self.draw_dots(code)
        self.select(newest if newest in self.skins else next(iter(self.skins), None))

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
        if not code or code in self.preparing or self.busy:
            return
        models, parts = self.skins[code], sb.part_textures(code)
        if self.preview_path(code).is_file() and not sb.preview_stale(code, parts):
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

    def fetch_names(self):
        codes = [c for c in self.skins if c not in self.names]
        if not codes:
            return

        def work():
            sb.resolve_names(codes)
            self.lines.put(("names", None))
        threading.Thread(target=work, daemon=True).start()

    def palette_of(self, code):
        if code not in self.palettes:
            self.palettes[code] = sb.choose_palette(code, sb.part_textures(code), None)
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

    # ------------------------------------------------------------ colori e anteprima

    def styles_of(self, code):
        if code not in self.style_cache:
            self.style_cache[code] = sb.palette_styles(code, sb.part_textures(code))
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
        return f"{self.code}   -   {style}{note}"

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
        for i, (name, cols) in enumerate(self.styles):
            card = tk.Frame(self.styles_box, bg=PANEL, cursor="hand2", highlightthickness=2, highlightbackground=BG)
            card.grid(row=i // 4, column=i % 4, padx=(0, 6), pady=(0, 6))
            sw = tk.Canvas(card, width=118, height=30, bg=PANEL, highlightthickness=0)
            sw.pack(padx=5, pady=(5, 2))
            sw.create_rectangle(0, 0, 70, 30, fill=hexcol(cols[0]), outline="")      # fondo: il colore piu' grande
            sw.create_rectangle(70, 0, 94, 30, fill=hexcol(cols[1]), outline="")
            sw.create_rectangle(94, 0, 118, 30, fill=hexcol(cols[2]), outline="")
            lbl = tk.Label(card, text=name, font=FONT, bg=PANEL, fg=TEXT)
            lbl.pack(pady=(0, 4))
            for widget in (card, sw, lbl):
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
        self.statues_lbl.config(text=("●  Statue: ON" if self.statues_on else "○  Statue: OFF"),
                                fg=accent if self.statues_on else MUTED)

    def toggle_statues(self):
        self.statues_on = not self.statues_on
        self._toggles()

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
        args = argparse.Namespace(colors=self.manual, layout=self.layout,
                                  no_statues=not self.statues_on, dry_run=False)

        def work():
            try:
                sb.safe_run(code, args)
            finally:
                self.lines.put(None)               # segnale di fine
        threading.Thread(target=work, daemon=True).start()

    def setup_map(self):
        """Una volta sola per progetto: crea i materiali del bot e collega muri e cielo."""
        if self.busy:
            return
        self.busy = True
        self.apply_btn.config(text="PREPARO LA MAPPA...", bg=ROW_SEL, fg=MUTED)

        def work():
            try:
                sb.safe_setup()
            finally:
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
