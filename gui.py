"""Interfaccia grafica di wallbot. Avvio: doppio click su genera.bat (o `python gui.py`)."""
import os
import random
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk

from PIL import Image, ImageTk

import wallbot as wb

PREVIEW = 640
PREVIEW_BG = (42, 42, 42)
NAMES = ("Base", "Colore A", "Colore B")


class App:
    def __init__(self, root):
        self.root = root
        root.title("wallbot - muri esagonali")
        root.resizable(False, False)
        self.rng = random.Random()
        self.colors = [wb.parse_hex(c) for c in ("ff7cd8", "ff00b4", "00ffff")]
        self.line = wb.parse_hex(wb.LINE_COLOR)
        self.bg = (255, 255, 255)
        self.layout = self._initial_layout()
        self.out_dir = wb.HERE / "out"

        self.canvas = tk.Label(root, bd=0, cursor="hand2")
        self.canvas.grid(row=0, column=0, padx=10, pady=10)
        self.canvas.bind("<Button-1>", lambda e: self.paint(e, +1))
        self.canvas.bind("<Button-3>", lambda e: self.paint(e, 0))

        side = ttk.Frame(root, padding=(0, 10, 10, 10))
        side.grid(row=0, column=1, sticky="ns")
        self._build_colors(side)
        self._build_layout(side)
        self._build_export(side)
        self.status = tk.StringVar(value="Click sinistro su una cella: base > A > B.  Click destro: torna base.")
        ttk.Label(root, textvariable=self.status, padding=(10, 0, 10, 8)).grid(
            row=1, column=0, columnspan=2, sticky="w")

        root.bind("<space>", lambda e: None if isinstance(e.widget, (ttk.Entry, ttk.Button)) else self.random_all())
        self.refresh()

    def _initial_layout(self):
        if (wb.LAYOUT_DIR / "original.json").is_file():
            return wb.load_layout("original")
        return wb.random_layout(self.rng)

    # ------------------------------------------------------------ pannelli

    def _build_colors(self, parent):
        box = ttk.LabelFrame(parent, text="Colori", padding=8)
        box.pack(fill="x")
        self.swatches, self.hex_vars, self.locks = [], [], []
        for i, name in enumerate(NAMES):
            ttk.Label(box, text=name, width=9).grid(row=i, column=0, sticky="w")
            sw = tk.Button(box, width=4, relief="solid", bd=1, command=lambda i=i: self.pick(i))
            sw.grid(row=i, column=1, padx=4, pady=2)
            var = tk.StringVar()
            ent = ttk.Entry(box, textvariable=var, width=8)
            ent.grid(row=i, column=2)
            ent.bind("<Return>", lambda e, i=i: self.typed(i))
            ent.bind("<FocusOut>", lambda e, i=i: self.typed(i))
            lock = tk.BooleanVar()
            ttk.Checkbutton(box, text="blocca", variable=lock).grid(row=i, column=3, padx=(6, 0))
            self.swatches.append(sw)
            self.hex_vars.append(var)
            self.locks.append(lock)

        ttk.Label(box, text="Linee", width=9).grid(row=3, column=0, sticky="w")
        self.line_sw = tk.Button(box, width=4, relief="solid", bd=1, command=self.pick_line)
        self.line_sw.grid(row=3, column=1, padx=4, pady=2)
        ttk.Label(box, text="Sfondo", width=9).grid(row=4, column=0, sticky="w")
        self.bg_sw = tk.Button(box, width=4, relief="solid", bd=1, command=self.pick_bg)
        self.bg_sw.grid(row=4, column=1, padx=4, pady=2)
        self.transparent = tk.BooleanVar(value=True)
        ttk.Checkbutton(box, text="trasparente", variable=self.transparent,
                        command=self.refresh).grid(row=4, column=2, columnspan=2, sticky="w")

        row = ttk.Frame(box)
        row.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        ttk.Button(row, text="Colori random", command=self.random_colors).pack(side="left", expand=True, fill="x")
        ttk.Button(row, text="Scambia A/B", command=self.swap_ab).pack(side="left", expand=True, fill="x", padx=(4, 0))

    def _build_layout(self, parent):
        box = ttk.LabelFrame(parent, text="Layout", padding=8)
        box.pack(fill="x", pady=8)
        self.preset = ttk.Combobox(box, state="readonly", width=16)
        self.preset.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.preset.bind("<<ComboboxSelected>>", self.load_preset)
        self.reload_presets()
        ttk.Button(box, text="Salva preset...", command=self.save_preset).grid(row=0, column=2, padx=(4, 0), sticky="ew")

        ttk.Label(box, text="Densita'").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.density = tk.DoubleVar(value=0.42)
        ttk.Scale(box, from_=0.1, to=0.8, variable=self.density).grid(
            row=1, column=1, columnspan=2, sticky="ew", pady=(6, 0))

        ttk.Button(box, text="Layout random", command=self.random_layout).grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Button(box, text="Pulisci", command=self.clear_layout).grid(
            row=2, column=2, padx=(4, 0), sticky="ew", pady=(6, 0))
        ttk.Button(box, text="Leggi layout da un PNG...", command=self.layout_from_png).grid(
            row=3, column=0, columnspan=3, sticky="ew", pady=(4, 0))
        ttk.Button(box, text="Tutto random  (spazio)", command=self.random_all).grid(
            row=4, column=0, columnspan=3, sticky="ew", pady=(4, 0))
        box.columnconfigure(1, weight=1)

    def _build_export(self, parent):
        box = ttk.LabelFrame(parent, text="Esporta", padding=8)
        box.pack(fill="x")
        ttk.Label(box, text="Lato (px)").grid(row=0, column=0, sticky="w")
        self.size = ttk.Combobox(box, values=("512", "1024", "1920", "2048", "4096"), width=7)
        self.size.set("1920")
        self.size.grid(row=0, column=1, sticky="w")
        self.mask = tk.BooleanVar()
        ttk.Checkbutton(box, text="anche maschera UEFN (_M)", variable=self.mask).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))

        ttk.Button(box, text="Salva questo muro", command=self.save_current).grid(
            row=2, column=0, columnspan=3, sticky="ew", pady=(8, 0))

        ttk.Separator(box).grid(row=3, column=0, columnspan=3, sticky="ew", pady=8)
        ttk.Label(box, text="Quanti").grid(row=4, column=0, sticky="w")
        self.count = tk.IntVar(value=10)
        ttk.Spinbox(box, from_=1, to=500, textvariable=self.count, width=6).grid(row=4, column=1, sticky="w")
        self.batch_new_layout = tk.BooleanVar(value=True)
        ttk.Checkbutton(box, text="layout diverso per ogni muro", variable=self.batch_new_layout).grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Button(box, text="Genera in serie (colori random)", command=self.batch).grid(
            row=6, column=0, columnspan=3, sticky="ew", pady=(6, 0))

        ttk.Separator(box).grid(row=7, column=0, columnspan=3, sticky="ew", pady=8)
        ttk.Button(box, text="Cartella...", command=self.choose_dir).grid(row=8, column=0, sticky="ew")
        ttk.Button(box, text="Apri cartella", command=self.open_dir).grid(
            row=8, column=1, columnspan=2, sticky="ew", padx=(4, 0))
        box.columnconfigure(2, weight=1)

    # ------------------------------------------------------------ anteprima

    def current_bg(self):
        return None if self.transparent.get() else self.bg

    def refresh(self):
        for sw, var, col in zip(self.swatches, self.hex_vars, self.colors):
            sw.config(bg="#" + wb.to_hex(col), activebackground="#" + wb.to_hex(col))
            var.set(wb.to_hex(col))
        self.line_sw.config(bg="#" + wb.to_hex(self.line))
        self.bg_sw.config(bg="#" + wb.to_hex(self.bg))
        wall = wb.render_wall(PREVIEW, self.layout, self.colors, self.line, self.current_bg())
        back = Image.new("RGBA", wall.size, PREVIEW_BG + (255,))
        back.alpha_composite(wall)
        self.photo = ImageTk.PhotoImage(back)
        self.canvas.config(image=self.photo)

    def paint(self, event, step):
        x, y = event.x * wb.REF / PREVIEW, event.y * wb.REF / PREVIEW
        cell = min(wb.PAINTABLE, key=lambda c: (wb.cell_center(*c)[0] - x) ** 2 + (wb.cell_center(*c)[1] - y) ** 2)
        cx, cy = wb.cell_center(*cell)
        if (cx - x) ** 2 + (cy - y) ** 2 > (wb.ROW_STEP / 2) ** 2:
            return
        new = (self.layout.get(cell, 0) + 1) % 3 if step else 0
        if new:
            self.layout[cell] = new
        else:
            self.layout.pop(cell, None)
        self.refresh()

    # ------------------------------------------------------------ colori

    def pick(self, i):
        rgb, _ = colorchooser.askcolor("#" + wb.to_hex(self.colors[i]), title=NAMES[i], parent=self.root)
        if rgb:
            self.colors[i] = tuple(int(v) for v in rgb)
            self.refresh()

    def pick_line(self):
        rgb, _ = colorchooser.askcolor("#" + wb.to_hex(self.line), title="Linee", parent=self.root)
        if rgb:
            self.line = tuple(int(v) for v in rgb)
            self.refresh()

    def pick_bg(self):
        rgb, _ = colorchooser.askcolor("#" + wb.to_hex(self.bg), title="Sfondo", parent=self.root)
        if rgb:
            self.bg = tuple(int(v) for v in rgb)
            self.transparent.set(False)
            self.refresh()

    def typed(self, i):
        try:
            col = wb.parse_hex(self.hex_vars[i].get())
        except Exception:
            self.hex_vars[i].set(wb.to_hex(self.colors[i]))
            return
        if col != self.colors[i]:
            self.colors[i] = col
            self.refresh()

    def new_palette(self):
        """Palette random che rispetta i colori bloccati."""
        fixed = [self.colors[0]] if self.locks[0].get() else []
        fresh = wb.random_palette(self.rng, fixed)
        return [old if lock.get() else new for old, new, lock in zip(self.colors, fresh, self.locks)]

    def random_colors(self):
        self.colors = self.new_palette()
        self.refresh()

    def swap_ab(self):
        self.colors[1], self.colors[2] = self.colors[2], self.colors[1]
        self.refresh()

    # ------------------------------------------------------------ layout

    def reload_presets(self):
        self.preset["values"] = sorted(p.stem for p in wb.LAYOUT_DIR.glob("*.json"))
        self.preset.set("preset...")

    def load_preset(self, _event=None):
        self.layout = wb.load_layout(self.preset.get())
        self.refresh()

    def save_preset(self):
        name = simpledialog.askstring("Salva preset", "Nome del layout:", parent=self.root)
        if not name:
            return
        name = "".join(ch for ch in name if ch.isalnum() or ch in "_-")
        if name:
            wb.save_layout(self.layout, name)
            self.reload_presets()
            self.status.set(f"Layout salvato come preset '{name}'.")

    def random_layout(self):
        self.layout = wb.random_layout(self.rng, self.density.get())
        self.refresh()

    def clear_layout(self):
        self.layout = {}
        self.refresh()

    def layout_from_png(self):
        path = filedialog.askopenfilename(parent=self.root, filetypes=[("Immagini", "*.png *.jpg *.jpeg")])
        if not path:
            return
        try:
            self.layout, found = wb.layout_from_image(path)
        except Exception as exc:
            messagebox.showerror("Errore", str(exc), parent=self.root)
            return
        if len(found) == 3 and messagebox.askyesno(
                "Colori", "Uso anche i colori di questo muro?", parent=self.root):
            self.colors = [wb.parse_hex(c) for c in found]
        self.refresh()

    def random_all(self):
        self.layout = wb.random_layout(self.rng, self.density.get())
        self.colors = self.new_palette()
        self.refresh()

    # ------------------------------------------------------------ export

    def get_size(self):
        try:
            size = int(self.size.get())
        except ValueError:
            size = 0
        if not 64 <= size <= 8192:
            messagebox.showerror("Errore", "Il lato deve essere un numero fra 64 e 8192.", parent=self.root)
            return None
        return size

    def write(self, size, layout, colors):
        self.out_dir.mkdir(parents=True, exist_ok=True)
        stem = "wall_" + "-".join(wb.to_hex(c) for c in colors)
        n = 1
        while (self.out_dir / f"{stem}_{n:02d}.png").exists():
            n += 1
        name = f"{stem}_{n:02d}"
        wb.render_wall(size, layout, colors, self.line, self.current_bg()).save(self.out_dir / f"{name}.png")
        if self.mask.get():
            wb.render_mask(size, layout).save(self.out_dir / f"{name}_M.png")
        return name

    def save_current(self):
        size = self.get_size()
        if size:
            name = self.write(size, self.layout, self.colors)
            self.status.set(f"Salvato {name}.png in {self.out_dir}")

    def batch(self):
        size = self.get_size()
        if not size:
            return
        try:
            total = max(1, int(self.count.get()))
        except (tk.TclError, ValueError):
            messagebox.showerror("Errore", "Numero di muri non valido.", parent=self.root)
            return
        for i in range(total):
            layout = (wb.random_layout(self.rng, self.density.get())
                      if self.batch_new_layout.get() else self.layout)
            self.write(size, layout, self.new_palette())
            self.status.set(f"Genero {i + 1}/{total}...")
            self.root.update()
        self.status.set(f"Fatto: {total} muri in {self.out_dir}")

    def choose_dir(self):
        path = filedialog.askdirectory(parent=self.root, initialdir=self.out_dir.parent)
        if path:
            self.out_dir = Path(path)
            self.status.set(f"Cartella di output: {self.out_dir}")

    def open_dir(self):
        self.out_dir.mkdir(parents=True, exist_ok=True)
        os.startfile(self.out_dir)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
