"""
Converte i .uemodel esportati da FortnitePorting in un unico FBX statico per UEFN.

Va eseguito dentro Blender, senza interfaccia:
  blender -b -P skin_convert.py -- <config.json>

config.json: {"out": "x.fbx", "models": [...uemodel], "textures": {"Body": "x_D.png", ...},
              "eye": "pupilla.png" (facoltativo)}

- Mette la skin in una posa rilassata (braccia abbassate) e la "cuoce" nella mesh.
- Le sezioni occhi delle skin cel-shaded (stesso materiale della testa, UV1 largo 2) diventano
  uno slot a parte con suffisso `_Eyes`, cosi' UEFN puo' dargli la texture delle pupille.
- Fa una foto della skin di fronte, su sfondo trasparente (`preview.png` accanto all'FBX).
- Misura i colori sulla superficie reale della skin (area dei triangoli x colore della texture
  in quel punto) e li scrive in `surface_colors.json` accanto all'FBX: serve per la palette.

Stampa una riga `SKIN_JSON {...}` con gli slot materiale della mesh risultante.
"""
import json
import math
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix

FP_PLUGIN = Path.home() / "AppData/Local/FortnitePorting/Plugins/Blender/fortnite_porting"
sys.path.insert(0, str(FP_PLUGIN))

from ueformat.importer.import_context import UEFormatImport  # noqa: E402
from ueformat.options import UEModelOptions  # noqa: E402

# osso -> (gradi verso il basso, gradi in avanti)
POSE = {
    "upperarm_l": (38, 6), "upperarm_r": (38, 6),
    "lowerarm_l": (10, 22), "lowerarm_r": (10, 22),
}
# la direzione di un arto va presa dall'osso figlio: nelle ossa importate da UE la "coda" non segue l'arto
LIMB_CHILD = {"upperarm_l": "lowerarm_l", "upperarm_r": "lowerarm_r", "lowerarm_l": "hand_l", "lowerarm_r": "hand_r"}
SAMPLE_SIZE = 256


def rotate_bone(arm, name, down_deg, forward_deg):
    """Ruota un osso in spazio armatura: verso il basso (-Z) e in avanti (-Y, il davanti della skin)."""
    bone = arm.pose.bones.get(name)
    child = arm.pose.bones.get(LIMB_CHILD.get(name, ""))
    if bone is None or child is None:
        return False
    for axis, deg, lowers in (("Y", down_deg, 2), ("X", forward_deg, 1)):
        if not deg:
            continue
        head = bone.head.copy()
        direction = (child.head - bone.head).normalized()
        best = None
        for sign in (1, -1):                      # il verso giusto e' quello che abbassa/avanza la mano
            rot = Matrix.Rotation(math.radians(sign * deg), 4, axis)
            moved = (rot.to_3x3() @ direction)[lowers]
            if best is None or moved < best[0]:
                best = (moved, rot)
        pivot = Matrix.Translation(head) @ best[1] @ Matrix.Translation(-head)
        bone.matrix = pivot @ bone.matrix
        bpy.context.view_layer.update()
        # alcune skin (es. gli animatronic) hanno il braccio pesato su ossa gemelle "retarget", che non
        # sono figlie dell'osso vero: senza la stessa rotazione il braccio resta fermo e la mano si stacca
        limb, side = name.rsplit("_", 1)
        twin = arm.pose.bones.get(f"{limb}_retarget_{'rt' if side == 'r' else 'lf'}")
        if twin is not None:
            twin.matrix = pivot @ twin.matrix
            bpy.context.view_layer.update()
    return True


def split_eye_slots(obj):
    """Slot duplicati con lo stesso materiale: quello con UV1 largo ~2 e' la sezione occhi."""
    mesh = obj.data
    seen = set()
    for idx, slot in enumerate(obj.material_slots):
        mat = slot.material
        if mat is None:
            continue
        if mat.name not in seen:
            seen.add(mat.name)
            continue
        suffix = f"_{idx}"
        if len(mesh.uv_layers) > 1:
            uv = mesh.uv_layers[1].data
            us = [uv[l].uv[0] for p in mesh.polygons if p.material_index == idx for l in p.loop_indices]
            if us and max(us) - min(us) > 1.5:
                suffix = "_Eyes"
        copy = mat.copy()
        copy.name = mat.name + suffix
        slot.material = copy


def pick_part(slot_name, parts):
    """Stessa regola di skinbot.pick_part: quale texture va su quale slot."""
    if not parts:
        return None
    low = slot_name.lower()
    inside = [p for p in parts if p.lower() in low]
    if inside:
        return max(inside, key=len)
    if "eye" in low and "Head" in parts:
        return "Head"
    return "Body" if "Body" in parts else next(iter(parts))


def load_pixels(path):
    img = bpy.data.images.load(str(path), check_existing=True)
    img.scale(SAMPLE_SIZE, SAMPLE_SIZE)
    arr = np.empty(SAMPLE_SIZE * SAMPLE_SIZE * 4, dtype=np.float32)
    img.pixels.foreach_get(arr)
    return arr.reshape(SAMPLE_SIZE, SAMPLE_SIZE, 4)        # riga 0 = v 0 (in basso), come le UV


def surface_colors(objects, textures):
    """Istogramma {(r,g,b) a 5 bit: area} dei colori visibili sulla superficie della skin."""
    cache, hist = {}, {}
    for obj in objects:
        mesh = obj.data
        if not mesh.uv_layers:
            continue
        uv = mesh.uv_layers[0].data
        slot_part = []
        for slot in obj.material_slots:
            name = slot.material.name if slot.material else ""
            slot_part.append(None if name.endswith("_Eyes") else pick_part(name, textures))
        for poly in mesh.polygons:
            part = slot_part[poly.material_index] if poly.material_index < len(slot_part) else None
            if part is None:
                continue
            if part not in cache:
                cache[part] = load_pixels(textures[part])
            u = sum(uv[l].uv[0] for l in poly.loop_indices) / poly.loop_total
            v = sum(uv[l].uv[1] for l in poly.loop_indices) / poly.loop_total
            px = cache[part][int((v % 1.0) * SAMPLE_SIZE) % SAMPLE_SIZE, int((u % 1.0) * SAMPLE_SIZE) % SAMPLE_SIZE]
            key = (int(px[0] * 31.99), int(px[1] * 31.99), int(px[2] * 31.99))
            hist[key] = hist.get(key, 0.0) + poly.area
    total = sum(hist.values()) or 1.0
    top = sorted(hist.items(), key=lambda kv: -kv[1])[:600]
    return [[round((k[0] + 0.5) * 8), round((k[1] + 0.5) * 8), round((k[2] + 0.5) * 8), round(w / total, 6)]
            for k, w in top]


def render_preview(mesh, textures, eye, path, glow=None):
    """Foto frontale della skin posata, sfondo trasparente: serve all'interfaccia di skinbot."""
    for slot in mesh.material_slots:
        mat = slot.material
        if mat is None:
            continue
        is_eye = mat.name.endswith("_Eyes")
        part = pick_part(mat.name, textures)
        if part is None:
            continue
        mat.use_nodes = True
        nodes, links = mat.node_tree.nodes, mat.node_tree.links
        nodes.clear()
        out = nodes.new("ShaderNodeOutputMaterial")
        bsdf = nodes.new("ShaderNodeBsdfPrincipled")
        bsdf.inputs["Roughness"].default_value = 0.75
        links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
        base = nodes.new("ShaderNodeTexImage")
        base.image = bpy.data.images.load(str(textures[part]), check_existing=False)
        color = base.outputs["Color"]
        if is_eye and eye and len(mesh.data.uv_layers) > 1:
            pupil = nodes.new("ShaderNodeTexImage")
            pupil.image = bpy.data.images.load(str(eye), check_existing=False)
            uv = nodes.new("ShaderNodeUVMap")
            if len(mesh.data.uv_layers) > 1:
                uv.uv_map = mesh.data.uv_layers[1].name
            links.new(uv.outputs["UV"], pupil.inputs["Vector"])
            mix = nodes.new("ShaderNodeMix")
            mix.data_type = "RGBA"
            links.new(pupil.outputs["Alpha"], mix.inputs["Factor"])
            links.new(base.outputs["Color"], mix.inputs["A"])
            links.new(pupil.outputs["Color"], mix.inputs["B"])
            color = mix.outputs["Result"]
        links.new(color, bsdf.inputs["Base Color"])
        if glow and part in glow and not is_eye:   # parti luminose (occhi che brillano, scritte): come in UEFN
            light = nodes.new("ShaderNodeTexImage")
            light.image = bpy.data.images.load(str(glow[part]), check_existing=False)
            links.new(light.outputs["Color"], bsdf.inputs["Emission Color"])
            bsdf.inputs["Emission Strength"].default_value = 1.5

    scene = bpy.context.scene
    height = mesh.dimensions[2]
    cam_data = bpy.data.cameras.new("PreviewCam")
    cam_data.type = "ORTHO"
    cam_data.ortho_scale = height * 1.06
    cam = bpy.data.objects.new("PreviewCam", cam_data)
    cam.location = (0.0, -10.0, height / 2)          # la skin guarda verso -Y
    cam.rotation_euler = (math.radians(90), 0.0, 0.0)
    scene.collection.objects.link(cam)
    scene.camera = cam
    sun = bpy.data.objects.new("PreviewSun", bpy.data.lights.new("PreviewSun", "SUN"))
    sun.data.energy = 2.5
    sun.rotation_euler = (math.radians(55), 0.0, math.radians(-25))
    scene.collection.objects.link(sun)
    world = bpy.data.worlds.new("PreviewWorld")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (1, 1, 1, 1)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.9
    scene.world = world
    engines = [e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items]
    scene.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in engines else "BLENDER_EEVEE"
    scene.render.film_transparent = True
    scene.render.resolution_x, scene.render.resolution_y = 420, 640
    scene.render.resolution_percentage = 100
    scene.view_settings.view_transform = "Standard"
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)


def bake_flipbook_face(objects, sheets, out, part="Head", base=None):
    """Disegna occhi, bocca e sopracciglia (prima espressione di ogni foglio) sulla testa, nello spazio UV1.

    Il gioco li compone con un materiale che qui non c'e': li si proietta di fronte sulla testa,
    in posizioni da viso (occhi a meta' altezza della faccia, bocca sotto, sopracciglia sopra).
    """
    if not base:
        return False
    head = next((o for o in objects for s in o.material_slots
                 if s.material and s.material.name.endswith("_" + part)), None)
    if head is None:
        return False
    me, mw = head.data, head.matrix_world
    slot = next(i for i, s in enumerate(head.material_slots) if s.material and s.material.name.endswith("_" + part))
    uv1 = me.uv_layers[0].data                     # la faccia si disegna nella texture della testa
    tris, pts = [], []
    me.calc_loop_triangles()
    for tri in me.loop_triangles:
        if tri.material_index != slot or (mw.to_3x3() @ tri.normal).y > -0.15:
            continue                              # solo le facce rivolte in avanti (-Y)
        co = [mw @ me.vertices[v].co for v in tri.vertices]
        uv = [uv1[l].uv for l in tri.loops]
        tris.append(([(u.x, u.y) for u in uv], [(c.x, c.z) for c in co]))
        pts += [(c.x, c.z) for c in co]
    if not tris:
        return
    xs = np.array([p[0] for p in pts])
    zs = np.array([p[1] for p in pts])
    width = np.percentile(xs, 97) - np.percentile(xs, 3)
    bottom, top = np.percentile(zs, 2), np.percentile(zs, 98)
    # monocolo/occhiali (slot Glass) = altezza degli occhi; senza, a 58% della faccia
    glass = [mw2 @ o.data.vertices[v].co for o in objects for mw2 in [o.matrix_world]
             for p in o.data.polygons if p.material_index < len(o.material_slots)
             and o.material_slots[p.material_index].material
             and "glass" in o.material_slots[p.material_index].material.name.lower() for v in p.vertices]
    eye_z = float(np.mean([g.z for g in glass])) if glass else bottom + 0.58 * (top - bottom)
    eye_dx = 0.24 * width
    layout = {                                     # (centro x, centro z, larghezza della cella)
        "Eyes": [(-eye_dx, eye_z, 0.62 * width), (eye_dx, eye_z, 0.62 * width)],
        "Brows": [(-eye_dx, eye_z + 0.24 * width, 0.62 * width), (eye_dx, eye_z + 0.24 * width, 0.62 * width)],
        "Mouths": [(0.0, eye_z - 0.42 * width, 0.95 * width)],
    }
    cells = {}
    for name, (path, n) in sheets.items():
        img = bpy.data.images.load(path, check_existing=True)
        w, h = img.size
        arr = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(arr)
        arr = arr.reshape(h, w, 4)                 # riga 0 = in basso
        cw, ch = w // n, h // n
        grid = [arr[h - (r + 1) * ch:h - r * ch, k * cw:(k + 1) * cw] for r in range(n) for k in range(n)]
        if name == "Brows":                        # sopracciglia: la prima espressione (neutra)
            cells[name] = grid[0]
        else:                                      # occhi e bocca: i piu' grandi, l'espressione "da statua"
            cells[name] = max(grid, key=lambda g: float(g[..., 3].sum()))
    iris = sheets.get("Iris")
    if iris and "Eyes" in cells:                   # il verde degli occhi e' il posto dell'iride: ci va la texture vera
        img = bpy.data.images.load(iris[0], check_existing=True)
        w, h = img.size
        tex = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(tex)
        tex = tex.reshape(h, w, 4)
        cell = cells["Eyes"].copy()
        green = (cell[..., 1] > 0.5) & (cell[..., 0] < 0.4) & (cell[..., 2] < 0.4) & (cell[..., 3] > 0.5)
        if green.any():
            ys, xs_ = np.nonzero(green)
            y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs_.min(), xs_.max() + 1
            gy = ((np.arange(y0, y1) - y0) / max(y1 - y0, 1) * h).astype(int).clip(0, h - 1)
            gx = ((np.arange(x0, x1) - x0) / max(x1 - x0, 1) * w).astype(int).clip(0, w - 1)
            patch = tex[np.ix_(gy, gx)]
            sub = cell[y0:y1, x0:x1]
            mask = green[y0:y1, x0:x1]
            sub[mask, :3] = patch[mask, :3]
            cells["Eyes"] = cell

    def decal(x, z):
        """Colore RGBA della faccia nel punto (x, z) di fronte alla testa."""
        rgba = np.zeros(x.shape + (4,), dtype=np.float32)
        for name in ("Mouths", "Eyes", "Brows"):
            if name not in cells:
                continue
            cell = cells[name]
            ch, cw = cell.shape[:2]
            for i, (cx, cz, cwid) in enumerate(layout[name]):
                u = (x - cx) / cwid + 0.5
                if name != "Mouths" and cx > 0:    # il secondo occhio e' lo specchio del primo
                    u = 1 - u
                v = (z - cz) / cwid + 0.5
                inside = (u >= 0) & (u < 1) & (v >= 0) & (v < 1)
                if not inside.any():
                    continue
                px = np.clip((u * cw).astype(int), 0, cw - 1)
                py = np.clip((v * ch).astype(int), 0, ch - 1)
                src = cell[py, px]
                a = np.where(inside, src[..., 3], 0)[..., None]
                rgba[..., :3] = rgba[..., :3] * (1 - a) + src[..., :3] * a
                rgba[..., 3:] = np.maximum(rgba[..., 3:], a)
        return rgba

    src = bpy.data.images.load(base, check_existing=False)
    sw, sh = src.size
    canvas = np.empty(sw * sh * 4, dtype=np.float32)
    src.pixels.foreach_get(canvas)
    canvas = canvas.reshape(sh, sw, 4)             # la texture della testa, riga 0 = v 0
    paint = np.zeros_like(canvas)
    for uv, xz in tris:
        uv = np.array(uv) * (sw, sh)
        xz = np.array(xz)
        x0, y0 = np.floor(uv.min(0)).astype(int)
        x1, y1 = np.ceil(uv.max(0)).astype(int)
        x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, sw - 1), min(y1, sh - 1)
        if x1 < x0 or y1 < y0:
            continue
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        (ax, ay), (bx, by), (cx_, cy_) = uv
        det = (by - cy_) * (ax - cx_) + (cx_ - bx) * (ay - cy_)
        if abs(det) < 1e-9:
            continue
        l1 = ((by - cy_) * (gx - cx_) + (cx_ - bx) * (gy - cy_)) / det
        l2 = ((cy_ - ay) * (gx - cx_) + (ax - cx_) * (gy - cy_)) / det
        l3 = 1 - l1 - l2
        inside = (l1 >= -0.01) & (l2 >= -0.01) & (l3 >= -0.01)
        if not inside.any():
            continue
        px = l1 * xz[0, 0] + l2 * xz[1, 0] + l3 * xz[2, 0]
        pz = l1 * xz[0, 1] + l2 * xz[1, 1] + l3 * xz[2, 1]
        col = decal(px, pz)
        region = paint[y0:y1 + 1, x0:x1 + 1]
        region[inside] = np.where(col[inside][:, 3:] > region[inside][:, 3:], col[inside], region[inside])
    a = paint[..., 3:]
    canvas[..., :3] = canvas[..., :3] * (1 - a) + paint[..., :3] * a
    canvas[..., 3] = 1.0
    out.parent.mkdir(parents=True, exist_ok=True)
    img = bpy.data.images.new("FlipbookFace", sw, sh, alpha=True)
    img.pixels.foreach_set(canvas.ravel())
    img.filepath_raw = str(out)
    img.file_format = "PNG"
    img.save()
    print("FACCIA flipbook:", out, "occhi a z", round(eye_z, 3), "larghezza", round(float(width), 3))
    return True


def main():
    cfg = json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text(encoding="utf-8"))
    out = Path(cfg["out"])
    textures = {k: v for k, v in cfg.get("textures", {}).items() if Path(v).is_file()}

    bpy.ops.wm.read_factory_settings(use_empty=True)
    options = UEModelOptions(link=True, import_sockets=False, import_morph_targets=False)
    for src in cfg["models"]:
        UEFormatImport(options).import_file(src)

    sources_mesh = [o for o in bpy.data.objects if o.type == "MESH"]
    if not sources_mesh:
        sys.exit("Nessuna mesh importata")
    for o in sources_mesh:
        split_eye_slots(o)
    out.parent.mkdir(parents=True, exist_ok=True)
    if cfg.get("flipbook") and cfg.get("face_out"):
        try:
            if bake_flipbook_face(sources_mesh, cfg["flipbook"], Path(cfg["face_out"]),
                                  cfg.get("face_slot") or "Head", textures.get(cfg.get("face_slot") or "Head")):
                textures[cfg.get("face_slot") or "Head"] = cfg["face_out"]
        except Exception as exc:                   # senza faccia la skin si converte lo stesso
            print("WARN faccia flipbook:", exc)
    colors = []
    try:
        colors = surface_colors(sources_mesh, textures)
        (out.parent / "surface_colors.json").write_text(json.dumps(colors))
    except Exception as exc:                       # la palette ha un ripiego, la conversione no
        print("WARN colori superficie:", exc)

    posed = 0
    for arm in [o for o in bpy.data.objects if o.type == "ARMATURE"]:
        for name, (down, forward) in POSE.items():
            posed += rotate_bone(arm, name, down, forward)
    bpy.context.view_layer.update()

    # Mesh statica: si cuoce la posa valutando i modificatori, poi via le armature.
    depsgraph = bpy.context.evaluated_depsgraph_get()
    meshes = []
    for o in sources_mesh:
        baked = bpy.data.meshes.new_from_object(o.evaluated_get(depsgraph), preserve_all_data_layers=True,
                                                depsgraph=depsgraph)
        new = bpy.data.objects.new(o.name + "_baked", baked)
        new.matrix_world = o.matrix_world.copy()
        bpy.context.scene.collection.objects.link(new)
        meshes.append(new)
    for o in [o for o in bpy.data.objects if o not in meshes]:
        bpy.data.objects.remove(o, do_unlink=True)

    bpy.ops.object.select_all(action="DESELECT")
    for o in meshes:
        o.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    if len(meshes) > 1:
        bpy.ops.object.join()
    mesh = bpy.context.view_layer.objects.active
    mesh.name = out.stem

    bpy.ops.object.select_all(action="DESELECT")
    mesh.select_set(True)
    # materiali senza facce (es. la "testa vuota" di certe skin): UEFN li salta e scala di un posto
    # tutti gli altri, cosi' il corpo finisce con la texture della testa. Via prima di esportare.
    # Vale anche per i materiali con sole facce schiacciate (area zero), che UEFN butta all'import.
    import bmesh
    area = {}
    for poly in mesh.data.polygons:
        area[poly.material_index] = area.get(poly.material_index, 0.0) + poly.area
    flat = {i for i, a in area.items() if a < max(1e-4, sum(area.values()) * 1e-4)}   # meno di 1 cm2: non si vede
    if flat:
        bm = bmesh.new()
        bm.from_mesh(mesh.data)
        bmesh.ops.delete(bm, geom=[f for f in bm.faces if f.material_index in flat], context="FACES")
        bm.to_mesh(mesh.data)
        bm.free()
    bpy.context.view_layer.objects.active = mesh
    bpy.ops.object.material_slot_remove_unused()
    bpy.ops.export_scene.fbx(
        filepath=str(out), use_selection=True, object_types={"MESH"},
        mesh_smooth_type="FACE", add_leaf_bones=False, bake_anim=False,
        axis_forward="-Z", axis_up="Y",
    )

    preview = False
    try:
        render_preview(mesh, textures, cfg.get("eye"), out.parent / "preview.png",
                       {k: v for k, v in cfg.get("emissive", {}).items() if Path(v).is_file()})
        preview = (out.parent / "preview.png").is_file()
    except Exception as exc:                       # la foto e' un extra: mai bloccare la conversione
        print("WARN anteprima:", exc)

    info = {
        "preview": preview,
        "fbx": str(out),
        "slots": [s.material.name if s.material else None for s in mesh.material_slots],
        "verts": len(mesh.data.vertices),
        "tris": sum(len(p.vertices) - 2 for p in mesh.data.polygons),
        "size_m": [round(v, 3) for v in mesh.dimensions],
        "uv_layers": [uv.name for uv in mesh.data.uv_layers],
        "posed_bones": posed,
        "surface_colors": len(colors),
    }
    print("SKIN_JSON " + json.dumps(info))


main()
