"""
updater - aggiornamento automatico di skinbot.

Il canale e' una cartella pubblicata online (l'indirizzo sta in update.json, voce "url") che
contiene `manifest.json` e i file dell'app:

  manifest.json = {"version": "1.2.0", "date": "...", "notes": "cosa e' cambiato",
                   "files": {"skinbot.py": {"sha256": "...", "size": 1234}, ...}}

Ogni file scaricato viene verificato con il suo sha256 prima di toccare l'installazione; i file
vecchi finiscono in `_backup` e vengono rimessi se qualcosa va storto a meta'.
Sul PC di sviluppo c'e' il file `.dev`: li' il bot non si aggiorna mai da solo.

  python updater.py --check            dice se c'e' una versione nuova
  python updater.py --apply            scarica, verifica e installa
  python updater.py --apply --relaunch ... e poi riapre la finestra di skinbot
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEV_MARKER = HERE / ".dev"
VERSION_FILE = HERE / "version.json"
CHANNEL_FILE = HERE / "update.json"
STAGING = HERE / "_update"
BACKUP = HERE / "_backup"
LOG_FILE = HERE / "skinbot.log"
TIMEOUT = 25
MAX_FILE = 50 * 1024 * 1024


class UpdateError(Exception):
    """Aggiornamento non riuscito: l'installazione e' rimasta com'era."""


def log(*parts):
    line = "[aggiornamento] " + " ".join(str(p) for p in parts)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line + "\n")
    except OSError:
        pass
    return line


def is_dev():
    return DEV_MARKER.exists()


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def local_version():
    data = read_json(VERSION_FILE, {}) or {}
    return str(data.get("version", "0.0.0"))


def channel_url():
    data = read_json(CHANNEL_FILE, {}) or {}
    url = str(data.get("url", "")).strip()
    if not url:
        return None
    return url if url.endswith("/") else url + "/"


def parse_version(text):
    parts = []
    for piece in str(text).strip().lstrip("vV").split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:4])


def fetch(url, nocache=False):
    if nocache:
        url += ("&" if "?" in url else "?") + "nocache=" + str(int(time.time()))
    req = urllib.request.Request(url, headers={"User-Agent": "skinbot-updater", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        data = resp.read(MAX_FILE + 1)
    if len(data) > MAX_FILE:
        raise UpdateError(f"file troppo grande: {url}")
    return data


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def safe_relative(name):
    """Un percorso del manifest deve restare dentro la cartella dell'app."""
    rel = Path(name)
    if rel.is_absolute() or ".." in rel.parts or not rel.parts or any(p in ("", ".") for p in rel.parts):
        raise UpdateError(f"percorso non ammesso nel manifest: {name!r}")
    if rel.parts[0].startswith("_") or rel.name in (".dev", "skinbot.log"):
        raise UpdateError(f"percorso riservato nel manifest: {name!r}")
    return rel


class Update:
    def __init__(self, manifest, url):
        self.manifest = manifest
        self.version = str(manifest.get("version", "0.0.0"))
        self.notes = str(manifest.get("notes", "") or "")
        self.files = manifest.get("files") or {}
        self.url = url
        if not isinstance(self.files, dict) or not self.files:
            raise UpdateError("manifest senza file")
        for name, info in self.files.items():
            safe_relative(name)
            if not isinstance(info, dict) or len(str(info.get("sha256", ""))) != 64:
                raise UpdateError(f"manifest senza sha256 per {name}")


def check():
    """L'aggiornamento disponibile, oppure None (anche in caso di rete assente: lo scrive nel log)."""
    if is_dev():
        return None
    url = channel_url()
    if not url:
        return None
    try:
        manifest = json.loads(fetch(url + "manifest.json", nocache=True).decode("utf-8"))
        update = Update(manifest, url)
    except Exception as exc:
        log("controllo fallito:", f"{type(exc).__name__}: {str(exc)[:160]}")
        return None
    if parse_version(update.version) > parse_version(local_version()):
        return update
    return None


def download(update):
    """Scarica tutti i file in `_update`, verificando ogni sha256. Niente viene installato."""
    if STAGING.exists():
        shutil.rmtree(STAGING, ignore_errors=True)
    STAGING.mkdir(parents=True)
    try:
        for name, info in update.files.items():
            rel = safe_relative(name)
            data = fetch(update.url + urllib.parse.quote(rel.as_posix()), nocache=True)
            if sha256(data) != info["sha256"] or ("size" in info and len(data) != int(info["size"])):
                raise UpdateError(f"file corrotto o non ancora aggiornato sul canale: {name}")
            target = STAGING / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        (STAGING / "manifest.json").write_text(json.dumps(update.manifest, indent=1), encoding="utf-8")
    except Exception:
        shutil.rmtree(STAGING, ignore_errors=True)
        raise
    log(f"scaricata la versione {update.version} ({len(update.files)} file)")
    return STAGING


def ready():
    """Un aggiornamento gia' scaricato e integro in `_update`, pronto da installare, oppure None."""
    manifest = read_json(STAGING / "manifest.json")
    if not manifest:
        return None
    try:
        update = Update(manifest, channel_url() or "")
        for name, info in update.files.items():
            path = STAGING / safe_relative(name)
            if not path.is_file() or sha256(path.read_bytes()) != info["sha256"]:
                return None
    except UpdateError:
        return None
    if parse_version(update.version) <= parse_version(local_version()):
        return None
    return update


def apply(staging=STAGING):
    """Installa i file scaricati. In caso di errore rimette quelli vecchi e rilancia l'eccezione."""
    update = ready() if staging == STAGING else Update(read_json(staging / "manifest.json", {}), "")
    if update is None:
        raise UpdateError("nessun aggiornamento integro da installare")
    if BACKUP.exists():
        shutil.rmtree(BACKUP, ignore_errors=True)
    BACKUP.mkdir(parents=True)
    written = []
    try:
        for name in update.files:
            rel = safe_relative(name)
            target = HERE / rel
            if target.is_file():
                (BACKUP / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, BACKUP / rel)
        for name in update.files:
            rel = safe_relative(name)
            target = HERE / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".nuovo")
            shutil.copyfile(staging / rel, tmp)
            os.replace(tmp, target)                 # sostituzione per file: o il vecchio o il nuovo
            written.append(rel)
    except Exception as exc:
        for rel in written:                         # torna indietro: rimette i file salvati
            saved = BACKUP / rel
            if saved.is_file():
                shutil.copyfile(saved, HERE / rel)
        for name in update.files:                   # via i file temporanei rimasti a meta'
            leftover = (HERE / safe_relative(name))
            leftover = leftover.with_name(leftover.name + ".nuovo")
            if leftover.exists():
                leftover.unlink()
        log("installazione fallita, ripristinata la versione precedente:", f"{type(exc).__name__}: {str(exc)[:160]}")
        raise UpdateError(f"installazione fallita, versione precedente ripristinata ({exc})") from exc
    shutil.rmtree(staging, ignore_errors=True)
    log(f"installata la versione {update.version}")
    return update.version


def pythonw():
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return candidate if candidate.is_file() else exe


def relaunch(version=None):
    """Riapre la finestra di skinbot con la versione appena installata."""
    cmd = [str(pythonw()), str(HERE / "skinbot_gui.py")]
    if version:
        cmd += ["--updated", str(version)]
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(cmd, cwd=str(HERE), close_fds=True, creationflags=flags,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="controlla se c'e' una versione nuova")
    ap.add_argument("--apply", action="store_true", help="scarica e installa la versione nuova")
    ap.add_argument("--relaunch", action="store_true", help="dopo --apply riapre la finestra")
    args = ap.parse_args()
    print(f"Versione installata: {local_version()}" + ("  (PC di sviluppo: niente aggiornamenti automatici)" if is_dev() else ""))
    if not (args.check or args.apply):
        ap.print_help()
        return 0
    update = check()
    if update is None:
        print("Nessun aggiornamento disponibile." if channel_url() else "Nessun canale di aggiornamento in update.json.")
        return 0
    print(f"Disponibile la versione {update.version}: {update.notes or 'nessuna nota'}")
    if not args.apply:
        return 0
    try:
        download(update)
        version = apply()
    except Exception as exc:
        print("ERRORE:", exc)
        return 1
    print(f"Installata la versione {version}.")
    if args.relaunch:
        relaunch(version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
