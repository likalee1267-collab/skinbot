"""
skinbot - da una skin esportata con FortnitePorting al set completo in UEFN.

Prima: in FortnitePorting esporta la skin su "Assets Folder", e tieni UEFN aperto sul progetto.

  python skinbot.py                 applica l'ultima skin esportata
  python skinbot.py WitchValve      applica la skin con quel nome in codice
  python skinbot.py --list          elenca le skin esportate
  python skinbot.py --watch         resta in attesa: applica ogni skin appena la esporti
  python skinbot.py --dry-run       mostra cosa farebbe, senza toccare UEFN
  python skinbot.py -c ff0000 222222 00ffff   colori scelti a mano invece che dalla skin
  python skinbot.py --layout v2     cambia anche il disegno degli esagoni (original, v2, v3, random)

Cosa fa: converte la mesh (Blender, senza finestre), la importa in UEFN con le texture,
ricava la palette dai colori reali della skin, ricolora rampa e cielo, mette la skin sulle
tre statue e salva. Ogni esecuzione viene registrata in skinbot.log.
"""
import argparse
import colorsys
import json
import random
import re
import subprocess
import sys
import time
import traceback
import urllib.request
from pathlib import Path

import wallbot as wb

HERE = Path(__file__).resolve().parent
FP_ASSETS = Path.home() / "AppData/Local/FortnitePorting/Assets"
MCP_URL = "http://127.0.0.1:8000/mcp"
LOG_FILE = HERE / "skinbot.log"

def configure(project):
    """Percorsi degli asset del bot dentro il progetto UEFN aperto (il nome lo dice UEFN)."""
    global ROOT, WALL_MI, SKY_MI, SKIN_MAT, EYE_MAT, REQUIRED_ASSETS
    ROOT = f"/{project}/WallBot"
    WALL_MI = f"{ROOT}/Materials/MI_HexWall_Rampa"
    SKY_MI = f"{ROOT}/Materials/MI_Skybox_Tema"
    SKIN_MAT = f"{ROOT}/Materials/M_Skin"
    EYE_MAT = f"{ROOT}/Materials/M_SkinEye"
    REQUIRED_ASSETS = (WALL_MI, SKIN_MAT, EYE_MAT)       # il cielo e' facoltativo


configure("redgotyfinal")
CONVERTER_VERSION = 3            # da alzare quando skin_convert.py cambia la mesh prodotta
STATUE_FOLDER = "WallBot/Skins"
RAMP_CENTER_Y = -159744
# (label, x, y, scala, yaw) - usati solo se le tre statue non esistono ancora
STATUE_SPOTS = [
    ("Skin_Centro", 139000, RAMP_CENTER_Y, 46, 90),
    ("Skin_Sinistra", 137500, RAMP_CENTER_Y - 4500, 38, 68),
    ("Skin_Destra", 137500, RAMP_CENTER_Y + 4500, 38, 112),
]
STATUE_Z = 224500
DEFAULT_PALETTE = [(255, 124, 216), (255, 0, 180), (0, 255, 255)]
# Cielo: nuvole rese monocromatiche e poi tinte, cosi' il colore regge anche sulle tinte calde.
SKY_SCALARS = (("Saturazione", 0.0), ("Luminosita", 5.0), ("Contrasto", 0.7))
EMISSIVE_STRENGTH = 1.5

T_ASSET = "editor_toolset.toolsets.asset.AssetTools"
T_TEX = "editor_toolset.toolsets.texture.TextureTools"
T_MESH = "editor_toolset.toolsets.static_mesh.StaticMeshTools"
T_MI = "editor_toolset.toolsets.material_instance.MaterialInstanceTools"
T_OBJ = "editor_toolset.toolsets.object.ObjectTools"
T_SCENE = "editor_toolset.toolsets.scene.SceneTools"

# Suffissi delle texture per tipo, in ordine di preferenza (CL = colore delle skin cel-shaded).
SUFFIXES = {
    "color": ["D", "CL", "BC", "BaseColor", "Diffuse", "Albedo", "C"],
    "normal": ["N", "Normal", "NRM"],
    "emissive": ["E", "Emissive"],
}
EYE_TEXTURE_NAMES = ["Eye", "Eyes", "Pupil_L", "Pupil", "Pupil_BC", "Iris", "Pupil_R"]


class BotError(Exception):
    """Errore spiegabile all'utente: il messaggio dice gia' cosa fare."""


def log(*parts):
    line = " ".join(str(p) for p in parts)
    print(line, flush=True)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line + "\n")
    except OSError:
        pass


def ref(path):
    """Content path -> riferimento oggetto, es. /A/B/Nome -> {"refPath": "/A/B/Nome.Nome"}."""
    return {"refPath": path if "." in path.rsplit("/", 1)[-1] else f"{path}.{path.rsplit('/', 1)[-1]}"}


def disk(path):
    return str(path).replace("\\", "/")


# ---------------------------------------------------------------- UEFN (MCP via HTTP)

class Uefn:
    def __init__(self, url=MCP_URL):
        self.url, self.sid, self.n = url, None, 0
        try:
            headers, _ = self._post({"method": "initialize", "params": {
                "protocolVersion": "2025-03-26", "capabilities": {},
                "clientInfo": {"name": "skinbot", "version": "2"}}})
        except OSError as exc:
            raise BotError(f"UEFN non risponde ({exc}). Apri UEFN sul progetto redgotyfinal e riprova.")
        self.sid = headers.get("Mcp-Session-Id")
        self._post({"method": "notifications/initialized"}, notify=True)

    def _post(self, body, notify=False):
        body = {"jsonrpc": "2.0", **body}
        if not notify:
            self.n += 1
            body["id"] = self.n
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.sid:
            headers["Mcp-Session-Id"] = self.sid
        req = urllib.request.Request(self.url, json.dumps(body).encode(), headers)
        with urllib.request.urlopen(req, timeout=300) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.headers, (json.loads(raw) if raw.strip() else {})

    def call(self, toolset, tool, **arguments):
        try:
            _, reply = self._post({"method": "tools/call", "params": {"name": "call_tool", "arguments": {
                "toolset_name": toolset, "tool_name": tool, "arguments": arguments}}})
        except OSError as exc:
            raise BotError(f"Collegamento con UEFN caduto durante '{tool}' ({exc}).")
        if "error" in reply:
            raise RuntimeError(f"{tool}: {reply['error']}")
        result = reply.get("result", {})
        text = "".join(c.get("text", "") for c in result.get("content", []))
        if result.get("isError"):
            raise RuntimeError(f"{tool}: {text[:400]}")
        try:
            return json.loads(text).get("returnValue")
        except (ValueError, AttributeError):
            return text or None

    def exists(self, path):
        return bool(self.call(T_ASSET, "exists", path=path))

    def preflight(self):
        level = self.call(T_SCENE, "get_current_level") or ""
        project = level.strip("/").split("/")[0]
        if not project:
            raise BotError("UEFN non ha una mappa aperta: apri il progetto e riprova.")
        configure(project)                        # funziona con qualsiasi nome di progetto
        missing = [p.rsplit("/", 1)[-1] for p in REQUIRED_ASSETS if not self.exists(p)]
        if missing:
            raise BotError(f"Il progetto '{project}' non e' ancora preparato per skinbot (mancano "
                           + ", ".join(missing) + "). Premi 'Prepara questa mappa' o lancia: python skinbot.py --setup")


# ---------------------------------------------------------------- skin su disco

def skin_code(stem):
    code = re.sub(r"^[MF]_(MED|LRG|SML)_", "", stem)
    return re.sub(r"_(Head|FaceAcc|Face|Hat|Hair|Body).*$", "", code)


def all_models():
    return [p for p in FP_ASSETS.rglob("*.uemodel") if "skeleton" not in p.stem.lower()]


def raw_skins():
    skins = {}
    for p in all_models():
        skins.setdefault(skin_code(p.stem), []).append(p)
    return {code: sorted(files) for code, files in skins.items()}


def base_code(code, codes):
    """Lo stile base di una variante: "SpireTend" per "SpireTend_Streak", se e' stato esportato."""
    bases = [c for c in codes if code.lower().startswith(c.lower() + "_")]
    return max(bases, key=len) if bases else None


def exported_skins():
    """{codice: [uemodel]} di tutte le skin esportate.

    Le varianti di stile escono da FortnitePorting con il solo corpo: testa e accessori del viso
    sono quelli dello stile base, quindi vengono presi da li'.
    """
    raw = raw_skins()
    skins = {}
    for code, files in raw.items():
        files = list(files)
        base = base_code(code, raw)
        if base and has_body(files) and not has_head(files):
            files += [p for p in raw[base] if not is_body(p)]
        if has_body(files) and not has_head(files):
            files += shared_head(files, raw)
        skins[code] = sorted(files)
    return skins


def shared_head(files, raw):
    """Testa generica (con un altro nome in codice) uscita nello stesso export del corpo."""
    stamp = max(p.stat().st_mtime for p in files)
    heads = [p for other in raw.values() if not has_body(other) for p in other
             if "Heads" in p.parts and abs(p.stat().st_mtime - stamp) < 120]
    return sorted(heads, key=lambda p: abs(p.stat().st_mtime - stamp))[:1]


def is_body(path):
    return "Bodies" in path.parts and "Parts" not in path.parts


def has_body(models):
    return any(is_body(p) for p in models)


def has_head(models):
    return any("Heads" in p.parts for p in models)


def latest_code(skins):
    """La skin esportata per ultima; tra varianti uscite insieme vale quella che ha il corpo."""
    if not skins:
        return None
    stamp = {code: max(p.stat().st_mtime for p in files) for code, files in skins.items()}
    newest = max(stamp.values())
    batch = [c for c in skins if newest - stamp[c] < 120]
    raw = raw_skins()
    with_body = [c for c in batch if has_body(skins[c])] or batch
    # tra gli stili usciti insieme vale quello base, che ha la sua testa
    own_head = [c for c in with_body if has_head(raw.get(c, []))] or with_body
    return max(own_head, key=lambda c: (stamp[c], len(skins[c])))


def part_textures(code, borrow=True):
    """{"Body": {"color": path, "normal": path, "emissive": path}, ...} per la skin."""
    best = {}
    # "SpruceBark" non deve prendersi le texture di "SpruceBark_Bob"
    longer = [c.lower() for c in exported_skins() if c.lower().startswith(code.lower() + "_")]
    for tex in sorted(FP_ASSETS.rglob(f"T_{code}_*.png")):
        match = re.match(rf"^T_{re.escape(code)}_(.+)$", tex.stem, flags=re.I)
        if not match or any(tex.stem.lower().startswith(f"t_{c}_") for c in longer):
            continue
        if skin_code(tex.parent.parent.name).lower() != code.lower():
            continue                               # texture di un'altra variante della stessa skin
        tokens = match.group(1).split("_")
        for kind, names in SUFFIXES.items():
            if tokens[-1] in names:
                part = "_".join(tokens[:-1]) or "Body"
                rank = names.index(tokens[-1])
                slot = best.setdefault(part, {})
                if kind not in slot or rank < slot[kind][0]:
                    slot[kind] = (rank, tex)
    parts = {part: {kind: tex for kind, (_, tex) in kinds.items()} for part, kinds in best.items()}
    parts = {part: kinds for part, kinds in parts.items() if "color" in kinds}
    base = base_code(code, raw_skins()) if borrow else None
    if base:                                       # testa e viso dello stile base, se la variante non li ha
        for part, kinds in part_textures(base, borrow=False).items():
            if part != "Body":
                parts.setdefault(part, kinds)
    return parts


def eye_texture(code):
    """Texture di pupilla/iride delle skin cel-shaded, se l'export ne ha una."""
    for owner in (code, base_code(code, raw_skins())):
        for name in EYE_TEXTURE_NAMES if owner else ():
            found = sorted(FP_ASSETS.rglob(f"T_{owner}_{name}.png"))
            if found:
                return found[0]
    return None


def auto_eye_texture(code, parts):
    """Pupilla disegnata dal bot per le skin cel-shaded esportate senza texture occhi.

    Un occhio per riquadro, centrato, su sfondo trasparente: sotto resta il bianco della testa.
    L'iride prende la tinta dominante della skin, scurita.
    """
    from PIL import Image, ImageDraw
    try:
        base = wb.skin_palette([k["color"] for k in parts.values()])[0]
        hue = colorsys.rgb_to_hsv(*(c / 255 for c in base))[0]
    except ValueError:
        hue = 0.58
    iris, rim, glow = wb.hsv(hue, 0.85, 0.62), wb.hsv(hue, 0.9, 0.22), wb.hsv(hue, 0.55, 0.95)
    k, size = 4, 512                               # disegno a 4x e riduco, per i bordi morbidi
    img = Image.new("RGBA", (size * k, size * k), (255, 255, 255, 0))
    d = ImageDraw.Draw(img)

    def ellipse(cx, cy, rx, ry, fill):
        d.ellipse([(cx - rx) * k, (cy - ry) * k, (cx + rx) * k, (cy + ry) * k], fill=fill)

    ellipse(256, 256, 96, 146, rim + (255,))
    ellipse(256, 256, 82, 132, iris + (255,))
    ellipse(256, 300, 62, 80, glow + (255,))       # riflesso chiaro in basso, stile anime
    ellipse(256, 250, 40, 72, (8, 8, 12, 255))
    ellipse(222, 186, 26, 26, (255, 255, 255, 255))
    ellipse(292, 318, 11, 11, (255, 255, 255, 255))
    folder = HERE / "skins" / code
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"T_{code}_AutoEye.png"
    img.resize((size, size), Image.LANCZOS).save(path)
    return path


def find_blender():
    found = sorted(Path("C:/Program Files/Blender Foundation").glob("Blender */blender.exe"))
    if not found:
        raise BotError("Blender non trovato in C:/Program Files/Blender Foundation: installalo e riprova.")
    return found[-1]


def convert(code, models, parts, eye=None):
    folder = HERE / "skins" / code
    folder.mkdir(parents=True, exist_ok=True)
    cfg = folder / "convert.json"
    cfg.write_text(json.dumps({
        "out": str(folder / f"SM_{code}.fbx"),
        "models": [str(m) for m in models],
        "textures": {part: str(kinds["color"]) for part, kinds in parts.items()},
        "eye": str(eye or eye_texture(code) or auto_eye_texture(code, parts)),
    }), encoding="utf-8")
    (folder / "surface_colors.json").unlink(missing_ok=True)
    cmd = [str(find_blender()), "-b", "-P", str(HERE / "skin_convert.py"), "--", str(cfg)]
    run = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    line = next((ln for ln in run.stdout.splitlines() if ln.startswith("SKIN_JSON ")), None)
    if not line:
        raise BotError("Conversione in Blender fallita:\n" + (run.stdout + run.stderr)[-1200:])
    return json.loads(line[len("SKIN_JSON "):])


# ---------------------------------------------------------------- colori

def choose_palette(code, parts, manual):
    """(colori, origine): manuale, misurata sulla superficie, dalle texture, o di ripiego."""
    if manual:
        return list(manual), "scelta a mano"
    measured = HERE / "skins" / code / "surface_colors.json"
    try:
        colors = json.loads(measured.read_text())
        if colors:
            return wb.palette_from_weights(colors), "misurata sulla superficie della skin"
    except (OSError, ValueError):
        pass
    try:
        return wb.skin_palette([k["color"] for k in parts.values()]), "dalle texture"
    except ValueError:
        return list(DEFAULT_PALETTE), "di ripiego (skin senza texture colore)"


def linear(rgb):
    def chan(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (chan(c) for c in rgb)
    return {"r": r, "g": g, "b": b, "a": 1.0}


def sky_tint(base):
    """Tinta del cielo: la tinta del colore base, appena schiarita verso il bianco."""
    h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in base))
    if s < 0.15:                                   # base neutra: cielo grigio-azzurro chiaro
        return {"r": 0.55, "g": 0.68, "b": 1.0, "a": 1.0}
    lin = linear(wb.hsv(h, max(s, 0.6), 1.0))
    top = max(lin["r"], lin["g"], lin["b"]) or 1.0
    return {k: (lin[k] / top) * 0.85 + 0.15 for k in "rgb"} | {"a": 1.0}


# ---------------------------------------------------------------- passi in UEFN

def pick_part(slot_part, parts):
    """Quale pezzo (Body, Head, ...) veste uno slot: il nome puo' stare ovunque (Anime_Head, BodyExtra)."""
    if not parts:
        return None
    low = slot_part.lower()
    inside = [p for p in parts if p.lower() in low]
    if inside:
        return max(inside, key=len)
    if "eye" in low and "Head" in parts:
        return "Head"
    return "Body" if "Body" in parts else next(iter(parts))


def import_skin(ue, code, fbx, parts, eye_tex, borrowed=False):
    folder = f"{ROOT}/Skins/Skin_{code}"
    # "_full": variante completata con la testa dello stile base (la mesh senza testa aveva lo stesso nome)
    name = f"SM_{code}_v{CONVERTER_VERSION}" + ("_full" if borrowed else "")
    mesh = f"{folder}/{name}"
    # UEFN non reimporta sopra un asset esistente: se cambia il convertitore cambia il nome
    if not ue.exists(mesh):
        ue.call(T_MESH, "import_file", folder_path=folder, asset_name=name, source_file=disk(fbx),
                import_materials=False, import_textures=False, combine_meshes=True)
        try:                                       # statue giganti: mai un ostacolo per i giocatori
            ue.call(T_MESH, "remove_collisions", mesh=ref(mesh))
        except RuntimeError as exc:
            log("  (collisione non rimossa:", str(exc)[:80] + ")")
    saved = [mesh]

    def texture(path, normal=False):
        asset = f"{folder}/Textures/{path.stem}"
        if not ue.exists(asset):
            ue.call(T_TEX, "import_file", folder_path=f"{folder}/Textures", asset_name=path.stem,
                    source_file=disk(path))
            if normal:
                ue.call(T_OBJ, "set_properties", instance=ref(asset),
                        values=json.dumps({"sRGB": False, "compressionSettings": "TC_Normalmap"}))
        saved.append(asset)
        return asset

    eye_asset = texture(eye_tex) if eye_tex else None
    for slot in ue.call(T_MESH, "get_material_slots", mesh=ref(mesh)):
        part = pick_part(slot.split(f"{code}_")[-1], parts)
        if not part:
            continue
        kinds = parts[part]
        is_eye = slot.endswith("_Eyes") and eye_asset
        mi = f"{folder}/MI_{code}_{part}" + ("_Eyes" if is_eye else "")
        if not ue.exists(mi):
            ue.call(T_MI, "create", folder_path=folder, asset_name=mi.rsplit("/", 1)[-1],
                    parent=ref(EYE_MAT if is_eye else SKIN_MAT))
        ue.call(T_MI, "set_texture_parameter", instance=ref(mi), name="BaseColorTex",
                value=ref(texture(kinds["color"])))
        if is_eye:
            ue.call(T_MI, "set_texture_parameter", instance=ref(mi), name="PupilTex", value=ref(eye_asset))
        else:
            if "normal" in kinds:
                ue.call(T_MI, "set_texture_parameter", instance=ref(mi), name="NormalTex",
                        value=ref(texture(kinds["normal"], normal=True)))
            if "emissive" in kinds:
                ue.call(T_MI, "set_texture_parameter", instance=ref(mi), name="EmissiveTex",
                        value=ref(texture(kinds["emissive"])))
            ue.call(T_MI, "set_scalar_parameter", instance=ref(mi), name="EmissiveStrength",
                    value=EMISSIVE_STRENGTH if "emissive" in kinds else 0.0)
        saved.append(mi)
        ue.call(T_MESH, "set_material", mesh=ref(mesh), slot_name=slot, material=ref(mi))
    ue.call(T_ASSET, "save_assets", asset_paths=sorted(set(saved)))
    return mesh


def apply_layout(ue, layout):
    """Cambia il disegno degli esagoni: genera la maschera, la importa se manca, la assegna."""
    if layout == "random":
        seed = random.randrange(100000)
        name, cells = f"rnd{seed}", wb.random_layout(random.Random(seed))
    else:
        name, cells = layout, wb.load_layout(layout)
    asset = f"{ROOT}/Textures/T_HexWall_{name}_M"
    if not ue.exists(asset):
        png = HERE / "uefn" / f"T_HexWall_{name}_M.png"
        png.parent.mkdir(exist_ok=True)
        wb.render_mask(2048, cells).save(png)
        ue.call(T_TEX, "import_file", folder_path=f"{ROOT}/Textures", asset_name=png.stem, source_file=disk(png))
        ue.call(T_OBJ, "set_properties", instance=ref(asset),
                values=json.dumps({"sRGB": False, "compressionSettings": "TC_Masks"}))
        ue.call(T_ASSET, "save_assets", asset_paths=[asset])
    ue.call(T_MI, "set_texture_parameter", instance=ref(WALL_MI), name="Mask", value=ref(asset))
    return name


def apply_theme(ue, colors):
    for name, col in zip(("ColorBase", "ColorA", "ColorB"), colors):
        ue.call(T_MI, "set_vector_parameter", instance=ref(WALL_MI), name=name, value=linear(col))
    if not ue.exists(SKY_MI):                     # mappa senza cielo tingibile: si ricolora solo la rampa
        ue.call(T_ASSET, "save_assets", asset_paths=[WALL_MI])
        return False
    ue.call(T_MI, "set_vector_parameter", instance=ref(SKY_MI), name="Tinta", value=sky_tint(colors[0]))
    for name, value in SKY_SCALARS:
        ue.call(T_MI, "set_scalar_parameter", instance=ref(SKY_MI), name=name, value=value)
    ue.call(T_ASSET, "save_assets", asset_paths=[WALL_MI, SKY_MI])
    return True


def place_statues(ue, mesh):
    try:
        existing = ue.call(T_SCENE, "get_actors_in_folder", folder_path=STATUE_FOLDER, recursive=False) or []
    except RuntimeError:                           # mappa nuova: la cartella delle statue non esiste ancora
        existing = []
    if existing:                                  # stesse statue, cambia solo la mesh
        for desc in existing:
            ue.call(T_OBJ, "set_properties", instance={"refPath": desc["actorPath"] + ".StaticMeshComponent0"},
                    values=json.dumps({"staticMesh": ref(mesh)}))
            ue.call(T_SCENE, "save_actor", actor={"refPath": desc["actorPath"]})
        return len(existing), "aggiornate"
    for label, x, y, scale, yaw in STATUE_SPOTS:
        actor = ue.call(T_SCENE, "add_to_scene_from_asset", asset_path=mesh, name=label, xform={
            "location": {"x": x, "y": y, "z": STATUE_Z},
            "rotation": {"pitch": 0, "yaw": yaw, "roll": 0},
            "scale": {"x": scale, "y": scale, "z": scale}})
        ue.call(T_SCENE, "set_actor_folder", actor=actor, folder_path=STATUE_FOLDER)
        ue.call(T_SCENE, "save_actor", actor=actor)
    return len(STATUE_SPOTS), "create"


# ---------------------------------------------------------------- flusso

def run_skin(code, args):
    skins = exported_skins()
    if not skins:
        raise BotError(f"Nessuna skin esportata in {FP_ASSETS}. In FortnitePorting esporta su 'Assets Folder'.")
    code = code or latest_code(skins)
    match = [c for c in skins if c.lower() == code.lower()]
    if not match:
        raise BotError(f"Skin '{code}' non trovata. Esportate: {', '.join(sorted(skins))}")
    code, models = match[0], skins[match[0]]
    parts = part_textures(code)
    log(f"Skin: {code}   ({len(models)} mesh, {len(parts)} pezzi con texture)")
    if not has_body(models):
        log("  Attenzione: questa variante non ha il corpo, la skin uscira' incompleta.")

    if args.dry_run:
        colors, origin = choose_palette(code, parts, args.colors)
        log("  Palette (%s): base=%s  A=%s  B=%s" % (origin, *(wb.to_hex(c) for c in colors)))
        log("  Dry run: convertirei", ", ".join(p.name for p in models))
        return

    ue = Uefn()                                   # prima di Blender: inutile convertire se UEFN e' chiuso
    ue.preflight()
    started = time.time()
    eye = eye_texture(code)
    generated = eye is None
    if generated:
        eye = auto_eye_texture(code, parts)
    info = convert(code, models, parts, eye)
    log(f"  Convertita: {info['tris']} triangoli, alta {info['size_m'][2]} m")
    # la pupilla va solo sulle sezioni occhi cel-shaded: slot "_Eyes" con un secondo canale UV
    eye_section = any((n or "").endswith("_Eyes") for n in info["slots"]) and len(info["uv_layers"]) > 1
    if not eye_section:
        eye = None
    elif generated:
        log("  Occhi: l'export non ha le pupille, le disegno io")
    colors, origin = choose_palette(code, parts, args.colors)
    log("  Palette (%s): base=%s  A=%s  B=%s" % (origin, *(wb.to_hex(c) for c in colors)))
    borrowed = any(skin_code(m.stem).lower() != code.lower() for m in models)
    if borrowed:
        log("  Testa e viso presi dallo stile base della skin")
    mesh = import_skin(ue, code, info["fbx"], parts, eye, borrowed)
    log("  Importata in UEFN:", mesh)
    if args.layout:
        log("  Disegno esagoni:", apply_layout(ue, args.layout))
    log("  Rampa e cielo ricolorati" if apply_theme(ue, colors) else "  Rampa ricolorata (cielo non collegato)")
    if not args.no_statues:
        count, what = place_statues(ue, mesh)
        log(f"  Statue {what}: {count}")
    log(f"Fatto e salvato in {time.time() - started:.0f} secondi.")


def safe_run(code, args):
    try:
        run_skin(code, args)
        return True
    except BotError as exc:
        log("ERRORE:", exc)
    except Exception as exc:                       # imprevisto: dettagli nel log, messaggio breve a schermo
        log("ERRORE imprevisto:", f"{type(exc).__name__}: {str(exc)[:300]}")
        try:
            with LOG_FILE.open("a", encoding="utf-8") as fh:
                fh.write(traceback.format_exc() + "\n")
        except OSError:
            pass
        log(f"  Dettagli in {LOG_FILE.name}")
    return False


def safe_setup():
    import skinbot_setup
    try:
        skinbot_setup.run()
        return True
    except BotError as exc:
        log("ERRORE:", exc)
    except Exception as exc:
        log("ERRORE imprevisto:", f"{type(exc).__name__}: {str(exc)[:300]}")
        try:
            with LOG_FILE.open("a", encoding="utf-8") as fh:
                fh.write(traceback.format_exc() + "\n")
        except OSError:
            pass
    return False


def export_stamp():
    files = all_models()
    return max((p.stat().st_mtime for p in files), default=0.0)


def watch(args):
    log("In attesa: esporta una skin da FortnitePorting su 'Assets Folder'. Ctrl+C per uscire.")
    seen = export_stamp()
    while True:
        time.sleep(2)
        stamp = export_stamp()
        if stamp <= seen:
            continue
        while True:                               # l'export scrive piu' file: aspetta che finisca
            time.sleep(4)
            again = export_stamp()
            if again == stamp:
                break
            stamp = again
        seen = stamp
        log("Nuovo export rilevato.")
        safe_run(None, args)
        log("In attesa del prossimo export...")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("skin", nargs="?", help="nome in codice della skin (default: l'ultima esportata)")
    ap.add_argument("-c", "--colors", type=wb.parse_hex, nargs=3, metavar="HEX", help="base A B, invece della palette automatica")
    ap.add_argument("--layout", help="disegno degli esagoni: original, v2, v3, random o un preset di wallbot")
    ap.add_argument("--no-statues", action="store_true", help="non toccare le tre statue")
    ap.add_argument("--dry-run", action="store_true", help="mostra il piano senza toccare UEFN")
    ap.add_argument("--list", action="store_true", help="elenca le skin esportate ed esce")
    ap.add_argument("--watch", action="store_true", help="resta in attesa e applica ogni nuovo export")
    ap.add_argument("--setup", action="store_true", help="prepara una volta il progetto UEFN aperto (materiali, muri, cielo)")
    args = ap.parse_args()

    if args.list:
        skins = exported_skins()
        newest = latest_code(skins)
        for code in sorted(skins):
            flags = ("" if has_body(skins[code]) else "  (senza corpo)") + ("  <- ultima" if code == newest else "")
            print(f"{code}{flags}")
        return 0
    if args.setup:
        return 0 if safe_setup() else 1
    if args.watch:
        try:
            watch(args)
        except KeyboardInterrupt:
            log("Fermato.")
        return 0
    return 0 if safe_run(args.skin, args) else 1


if __name__ == "__main__":
    sys.exit(main())
