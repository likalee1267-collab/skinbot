"""
Prepara un progetto UEFN per skinbot: una volta sola, su una copia della mappa con la rampa.

  python skinbot.py --setup

Crea la cartella WallBot con i materiali del bot, collega muri, pavimenti e rampe al materiale
ricolorabile e il cielo della rampa a un materiale tingibile. Si puo' rilanciare senza danni:
quello che esiste gia' viene lasciato com'e'. Le tre statue le crea skinbot alla prima skin.
"""
import json

import skinbot as sb
import wallbot as wb

T_MAT = "editor_toolset.toolsets.material.MaterialTools"
EXPR = "/Script/Engine.MaterialExpression"
OLD_WALL_MATERIAL = "dbdbd_4_1_4_1_1_Mat"           # il materiale rosa originale di muri e pavimenti
WALL_LABELS = ("BrickSimple AAA SolidWall", "Floors Generic BasicTile", "Obstacle Course RoofS")
RAMP_POINT = (130000, sb.RAMP_CENTER_Y, 229000)     # un punto dentro la rampa, per trovare il suo cielo
WALL_DEFAULTS = (("ColorBase", "ff7cd8"), ("ColorA", "ff00b4"), ("ColorB", "00ffff"),
                 ("ColorLine", "64748b"), ("ColorBorder", "ffffff"))


def props(ue, obj, **values):
    ue.call(sb.T_OBJ, "set_properties", instance=obj, values=json.dumps(values))


def node(ue, mat, cls, x, y, **values):
    expr = ue.call(T_MAT, "add_expression", material_or_function=sb.ref(mat),
                   expression_class={"refPath": EXPR + cls}, x=x, y=y)
    if values:
        props(ue, expr, **values)
    return expr


def link(ue, a, out, b, inp):
    ue.call(T_MAT, "connect_expressions", from_expression=a, from_output_name=out, to_expression=b, to_input_name=inp)


def output(ue, expr, out, prop):
    ue.call(T_MAT, "connect_to_output", expression=expr, output_name=out, material_property=prop)


def finish(ue, mat):
    ue.call(T_MAT, "layout_expressions", material_or_function=sb.ref(mat))
    ue.call(T_MAT, "recompile", material_or_function=sb.ref(mat))
    ue.call(sb.T_ASSET, "save_assets", asset_paths=[mat])


def new_material(ue, path):
    folder, name = path.rsplit("/", 1)
    ue.call(T_MAT, "create_material", folder_path=folder, asset_name=name)


def texture(ue, name, image, **settings):
    """Importa una texture generata dal bot, se manca. Ritorna il content path."""
    asset = f"{sb.ROOT}/Textures/{name}"
    if not ue.exists(asset):
        png = sb.HERE / "uefn" / f"{name}.png"
        png.parent.mkdir(exist_ok=True)
        image.save(png)
        ue.call(sb.T_TEX, "import_file", folder_path=f"{sb.ROOT}/Textures", asset_name=name, source_file=sb.disk(png))
        if settings:
            props(ue, sb.ref(asset), **settings)
        ue.call(sb.T_ASSET, "save_assets", asset_paths=[asset])
    return asset


# ---------------------------------------------------------------- materiali

def build_wall_material(ue, mask):
    mat = f"{sb.ROOT}/Materials/M_HexWall"
    if ue.exists(mat):
        return False
    new_material(ue, mat)
    ts = node(ue, mat, "TextureSampleParameter2D", -900, 300, parameterName="Mask", group="Layout",
              texture=sb.ref(mask), samplerType="SAMPLERTYPE_Masks")
    colors = {}
    for i, (name, hx) in enumerate(WALL_DEFAULTS):
        colors[name] = node(ue, mat, "VectorParameter", -600, i * 200, parameterName=name, group="Colors",
                            sortPriority=i, defaultValue=sb.linear(wb.parse_hex(hx)))
    lerps = [node(ue, mat, "LinearInterpolate", -300 + i * 200, i * 200) for i in range(4)]
    link(ue, colors["ColorBase"], "", lerps[0], "A"); link(ue, colors["ColorA"], "", lerps[0], "B"); link(ue, ts, "R", lerps[0], "Alpha")
    link(ue, lerps[0], "", lerps[1], "A"); link(ue, colors["ColorB"], "", lerps[1], "B"); link(ue, ts, "G", lerps[1], "Alpha")
    link(ue, lerps[1], "", lerps[2], "A"); link(ue, colors["ColorLine"], "", lerps[2], "B"); link(ue, ts, "B", lerps[2], "Alpha")
    link(ue, colors["ColorBorder"], "", lerps[3], "A"); link(ue, lerps[2], "", lerps[3], "B"); link(ue, ts, "A", lerps[3], "Alpha")
    strength = node(ue, mat, "ScalarParameter", 300, 700, parameterName="EmissiveStrength", group="Look", defaultValue=0.0)
    glow = node(ue, mat, "Multiply", 500, 600)
    link(ue, lerps[3], "", glow, "A"); link(ue, strength, "", glow, "B")
    rough = node(ue, mat, "ScalarParameter", 300, 900, parameterName="Roughness", group="Look", defaultValue=0.9)
    output(ue, lerps[3], "", "MP_BaseColor")
    output(ue, glow, "", "MP_EmissiveColor")
    output(ue, rough, "", "MP_Roughness")
    finish(ue, mat)
    return True


def build_skin_material(ue, black, flat_normal):
    mat = sb.SKIN_MAT
    if ue.exists(mat):
        return False
    new_material(ue, mat)
    base = node(ue, mat, "TextureSampleParameter2D", -700, 0, parameterName="BaseColorTex", group="Skin", texture=sb.ref(black))
    rough = node(ue, mat, "ScalarParameter", -700, 300, parameterName="Roughness", group="Skin", defaultValue=0.7)
    glow_tex = node(ue, mat, "TextureSampleParameter2D", -700, 600, parameterName="EmissiveTex", group="Skin", texture=sb.ref(black))
    strength = node(ue, mat, "ScalarParameter", -700, 900, parameterName="EmissiveStrength", group="Skin", defaultValue=0.0)
    glow = node(ue, mat, "Multiply", -350, 700)
    link(ue, glow_tex, "RGB", glow, "A"); link(ue, strength, "", glow, "B")
    normal = node(ue, mat, "TextureSampleParameter2D", -700, 1100, parameterName="NormalTex", group="Skin",
                  texture=sb.ref(flat_normal), samplerType="SAMPLERTYPE_Normal")
    output(ue, base, "RGB", "MP_BaseColor")
    output(ue, rough, "", "MP_Roughness")
    output(ue, glow, "", "MP_EmissiveColor")
    output(ue, normal, "RGB", "MP_Normal")
    finish(ue, mat)
    return True


def build_eye_material(ue, black):
    mat = sb.EYE_MAT
    if ue.exists(mat):
        return False
    new_material(ue, mat)
    base = node(ue, mat, "TextureSampleParameter2D", -700, 0, parameterName="BaseColorTex", group="Skin", texture=sb.ref(black))
    pupil = node(ue, mat, "TextureSampleParameter2D", -700, 350, parameterName="PupilTex", group="Skin", texture=sb.ref(black))
    uv1 = node(ue, mat, "TextureCoordinate", -1000, 350, coordinateIndex=1)
    link(ue, uv1, "", pupil, "UVs")
    mix = node(ue, mat, "LinearInterpolate", -300, 100)
    link(ue, base, "RGB", mix, "A"); link(ue, pupil, "RGB", mix, "B"); link(ue, pupil, "A", mix, "Alpha")
    rough = node(ue, mat, "ScalarParameter", -300, 500, parameterName="Roughness", group="Skin", defaultValue=0.6)
    output(ue, mix, "", "MP_BaseColor")
    output(ue, rough, "", "MP_Roughness")
    finish(ue, mat)
    return True


# ---------------------------------------------------------------- livello

def connect_walls(ue):
    """Muri, pavimenti e rampe che usano ancora il materiale rosa passano a quello ricolorabile."""
    changed, seen = 0, set()
    for label in WALL_LABELS:
        for desc in ue.call(sb.T_SCENE, "find_actors", collision_channels=[], name=label) or []:
            path = desc["actorPath"]
            if path in seen:
                continue
            seen.add(path)
            comp = {"refPath": path + ".StaticMeshComponent0"}
            try:
                mats = json.loads(ue.call(sb.T_OBJ, "get_properties", instance=comp,
                                          properties=["overrideMaterials"])).get("overrideMaterials") or []
            except RuntimeError:
                continue
            if not any(m and OLD_WALL_MATERIAL in m.get("refPath", "") for m in mats):
                continue
            new = [sb.ref(sb.WALL_MI) if (m and OLD_WALL_MATERIAL in m.get("refPath", "")) else m for m in mats]
            props(ue, comp, overrideMaterials=new)
            ue.call(sb.T_SCENE, "save_actor", actor={"refPath": path})
            changed += 1
    return changed


def connect_sky(ue):
    """La barriera gigante che fa da cielo alla rampa passa a una copia tingibile del suo materiale."""
    x, y, z = RAMP_POINT
    box = {"min": {"x": x - 500, "y": y - 500, "z": z - 500}, "max": {"x": x + 500, "y": y + 500, "z": z + 500}, "isValid": True}
    for desc in ue.call(sb.T_SCENE, "find_actors", collision_channels=[], bounds=box) or []:
        size = desc["bounds"]["max"]["x"] - desc["bounds"]["min"]["x"]
        if "Device_Barrier" not in desc["class"]["refPath"] or size < 15000:
            continue
        actor = {"refPath": desc["actorPath"]}
        current = (json.loads(ue.call(sb.T_OBJ, "get_properties", instance=actor,
                                      properties=["barrierMaterial"])).get("barrierMaterial") or {}).get("refPath", "")
        if not current:
            continue
        if current.split(".")[0] == sb.SKY_MI:
            return "gia' collegato"
        if not ue.exists(sb.SKY_MI):
            params = [p["name"] for p in ue.call(sb.T_MI, "list_parameters", material={"refPath": current}) or []]
            if "Tinta" not in params:
                return f"cielo non tingibile (materiale {current.split('.')[-1]} senza parametro Tinta)"
            ue.call(sb.T_ASSET, "duplicate", path=current.split(".")[0], new_path=sb.SKY_MI)
            ue.call(sb.T_ASSET, "save_assets", asset_paths=[sb.SKY_MI])
        props(ue, actor, barrierMaterial=sb.ref(sb.SKY_MI))
        ue.call(sb.T_SCENE, "save_actor", actor=actor)
        return f"collegato ({desc['label']})"
    return "nessuna barriera-cielo trovata intorno alla rampa"


# ---------------------------------------------------------------- flusso

def build_assets(ue):
    from PIL import Image
    mask = texture(ue, "T_HexWall_original_M", wb.render_mask(2048, wb.load_layout("original")),
                   sRGB=False, compressionSettings="TC_Masks")
    black = texture(ue, "T_WB_Black", Image.new("RGB", (8, 8), (0, 0, 0)))
    flat = texture(ue, "T_WB_FlatNormal", Image.new("RGB", (8, 8), (128, 128, 255)),
                   sRGB=False, compressionSettings="TC_Normalmap")
    made = []
    if build_wall_material(ue, mask):
        made.append("M_HexWall")
    if not ue.exists(sb.WALL_MI):
        folder, name = sb.WALL_MI.rsplit("/", 1)
        ue.call(sb.T_MI, "create", folder_path=folder, asset_name=name, parent=sb.ref(f"{sb.ROOT}/Materials/M_HexWall"))
        ue.call(sb.T_ASSET, "save_assets", asset_paths=[sb.WALL_MI])
        made.append(name)
    if build_skin_material(ue, black, flat):
        made.append("M_Skin")
    if build_eye_material(ue, black):
        made.append("M_SkinEye")
    return made


def run(assets_only=False):
    ue = sb.Uefn()
    level = ue.call(sb.T_SCENE, "get_current_level") or ""
    project = level.strip("/").split("/")[0]
    if not project:
        raise sb.BotError("UEFN non ha una mappa aperta: apri il progetto e riprova.")
    if not getattr(run, "keep_root", False):
        sb.configure(project)
    sb.log(f"Preparo il progetto '{project}' in {sb.ROOT}")
    made = build_assets(ue)
    sb.log("  Materiali creati:", ", ".join(made) if made else "nessuno, c'erano gia'")
    if assets_only:
        return
    sb.log("  Muri, pavimenti e rampe collegati:", connect_walls(ue))
    sb.log("  Cielo:", connect_sky(ue))
    sb.log("Mappa pronta: esporta una skin e premi APPLICA A UEFN.")
