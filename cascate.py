"""
Cascate XP: tre cascate di UEFN dietro la fine della rampa, coperte dagli stemmi XP della mappa.

  python cascate.py            le crea sulla mappa aperta (quelle che ci sono gia' restano)
  python cascate.py --remove   le toglie

La cascata e' l'oggetto di Fortnite (CP_Waterfall_02b); lo stemma e' la mesh che usano gli XP
raccoglibili della rampa. Sono solo scenografia: non danno XP e non hanno collisioni proprie.
"""
import json
import sys

import skinbot as sb

FOLDER = "WallBot/Cascate"
WATERFALL_CLASS = "/CRG_Props/SetupAssets/Blueprints/CP_Waterfall_02b.CP_Waterfall_02b_C"
SPOTS = [("Centro", 0), ("Sinistra", -5600), ("Destra", 5600)]
# misure prese sulla mappa: con queste scale il velo d'acqua e' largo ~4800 e va da z 237000 a 223000
FALL_X, FALL_Z, FALL_YAW = 145500, 237650, 90    # a yaw 90 il lato bombato guarda la rampa
FALL_SCALE = {"x": 7.4, "y": 3.0, "z": 12.8}
BADGE_X = 144900                                   # appena davanti al velo d'acqua (che sta a x 145100)
BADGE_COLUMNS = (-1150, 1150)
BADGE_ROWS = [235300 - i * 1900 for i in range(7)]
BADGE_WIDTH = 1750                                 # larghezza dello stemma sulla cascata
RAMP_BOX = ((120500, -161500, 225000), (137000, -158000, 234500))


def badge_mesh(ue):
    """La mesh dello stemma XP: quella degli oggetti raccoglibili della rampa. None se non ce ne sono."""
    lo, hi = RAMP_BOX
    box = {"min": dict(zip("xyz", lo)), "max": dict(zip("xyz", hi)), "isValid": True}
    tally = {}
    for desc in ue.call(sb.T_SCENE, "find_actors", collision_channels=[], bounds=box) or []:
        if "Collectible" not in ((desc.get("class") or {}).get("refPath") or ""):
            continue
        try:
            got = json.loads(ue.call(sb.T_OBJ, "get_properties", instance={"refPath": desc["actorPath"]},
                                     properties=["customMesh"]))
        except RuntimeError:
            continue
        path = ((got.get("customMesh") or {}).get("refPath") or "").split(".")[0]
        if path:
            tally[path] = tally.get(path, 0) + 1
        if sum(tally.values()) >= 12:               # bastano pochi campioni
            break
    return max(tally, key=tally.get) if tally else None


def badge_transform(ue, mesh, y, z):
    """Lo stemma e' una piastrina: la faccia larga va verso la rampa, qualunque asse abbia la mesh."""
    b = ue.call(sb.T_MESH, "get_bounds", mesh=sb.ref(mesh))
    ex, ey = b["max"]["x"] - b["min"]["x"], b["max"]["y"] - b["min"]["y"]
    scale = BADGE_WIDTH / max(ex, ey, 1)
    return {"location": {"x": BADGE_X, "y": y, "z": z},
            "rotation": {"pitch": 0, "yaw": 0 if ey >= ex else 90, "roll": 0},
            "scale": {"x": scale, "y": scale, "z": scale}}


def existing(ue):
    try:
        return ue.call(sb.T_SCENE, "get_actors_in_folder", folder_path=FOLDER, recursive=False) or []
    except RuntimeError:                           # la cartella non esiste ancora
        return []


def spawn(ue, label, xform, **source):
    tool = "add_to_scene_from_asset" if "asset_path" in source else "add_to_scene_from_class"
    actor = ue.call(sb.T_SCENE, tool, name=label, xform=xform, **source)
    ue.call(sb.T_ACTOR, "set_actor_transform", actor=actor, xform=xform)   # alla creazione UEFN puo' ignorare rotazione e scala
    ue.call(sb.T_ACTOR, "set_label", actor=actor, label=label)
    ue.call(sb.T_SCENE, "set_actor_folder", actor=actor, folder_path=FOLDER)
    ue.call(sb.T_SCENE, "save_actor", actor=actor)


def place(ue):
    """Crea quello che manca. Ritorna (cascate create, stemmi creati, nota)."""
    have = {d.get("label") for d in existing(ue)}
    mesh = badge_mesh(ue)
    falls = badges = 0
    for name, offset in SPOTS:
        y = sb.RAMP_CENTER_Y + offset
        if f"Cascata_{name}" not in have:
            spawn(ue, f"Cascata_{name}", {"location": {"x": FALL_X, "y": y, "z": FALL_Z},
                                          "rotation": {"pitch": 0, "yaw": FALL_YAW, "roll": 0}, "scale": dict(FALL_SCALE)},
                  actor_type={"refPath": WATERFALL_CLASS})
            falls += 1
        if not mesh:
            continue
        for row, z in enumerate(BADGE_ROWS):
            for col, dy in enumerate(BADGE_COLUMNS):
                label = f"XP_{name}_{row}_{col}"
                if label not in have:
                    spawn(ue, label, badge_transform(ue, mesh, y + dy, z), asset_path=mesh)
                    badges += 1
    note = "" if mesh else "stemmi XP non messi: sulla rampa non trovo oggetti raccoglibili con una mesh loro"
    return falls, badges, note


def remove(ue):
    """Toglie solo gli oggetti creati dal bot: quelli con le sue etichette, nella sua cartella."""
    gone = 0
    for desc in existing(ue):
        if (desc.get("label") or "").startswith(("Cascata_", "XP_")):
            ue.call(sb.T_SCENE, "remove_from_scene", actor={"refPath": desc["actorPath"]})
            gone += 1
    return gone


def main():
    ue = sb.Uefn()
    level = ue.call(sb.T_SCENE, "get_current_level") or ""
    sb.configure(level.strip("/").split("/")[0])
    if "--remove" in sys.argv:
        print("Tolti:", remove(ue))
    else:
        print("Cascate, stemmi, nota:", place(ue))


if __name__ == "__main__":
    main()
