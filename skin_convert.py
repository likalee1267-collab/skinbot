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


def render_preview(mesh, textures, eye, path):
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
    bpy.ops.export_scene.fbx(
        filepath=str(out), use_selection=True, object_types={"MESH"},
        mesh_smooth_type="FACE", add_leaf_bones=False, bake_anim=False,
        axis_forward="-Z", axis_up="Y",
    )

    preview = False
    try:
        render_preview(mesh, textures, cfg.get("eye"), out.parent / "preview.png")
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
