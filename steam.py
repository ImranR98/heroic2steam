"""Steam integration: binary shortcuts.vdf parsing/editing, grid art cleanup,
and Steam process control (launch, add-non-steam URL, appid lookup)."""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

STEAM_DIR = Path(os.environ.get("STEAM_DIR", Path.home() / ".local/share/Steam"))
APP_DIR = Path.home() / ".local/share/applications/steam-add-nonsteam-game"
SENTINEL = Path(tempfile.gettempdir()) / "addnonsteamgamefile"

log = print


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def vdf_paths():
    yield from sorted(STEAM_DIR.glob("userdata/*/config/shortcuts.vdf"))


def read_entries(vdf: Path):
    """Yield (fields, start, end) per shortcut in a binary shortcuts.vdf."""
    data = vdf.read_bytes()
    p = 0

    def nullstr():
        nonlocal p
        i = data.index(0, p)
        v = data[p:i].decode("utf-8", "replace")
        p = i + 1
        return v

    if data[p : p + 1] == b"\x00":
        p += 1
    if nullstr() != "shortcuts":
        return
    while p < len(data):
        if data[p] != 0x00:
            break
        start = p
        p += 1
        nullstr()  # index key
        fields = {}
        while p < len(data) and data[p] in (0x01, 0x02):
            t = data[p]
            p += 1
            key = nullstr()
            if t == 0x01:
                fields[key] = nullstr()
            else:
                fields[key] = struct.unpack("<I", data[p : p + 4])[0]
                p += 4
        if data[p : p + 6] == b"\x00tags\x00":
            p += 6
            while p < len(data) and data[p] != 0x08:
                p += 1
            p += 1
        if p < len(data) and data[p] == 0x08:
            p += 1
        yield fields, start, p


def _rewrite(vdf: Path, data: bytes) -> None:
    vdf.with_name(vdf.name + ".bak").write_bytes(vdf.read_bytes())
    tmp = vdf.with_name(vdf.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, vdf)


def edit_entries(vdf: Path, fn) -> int:
    """Call fn(fields, start, raw) per entry: None drops it, bytes keeps it
    unchanged, (bytes, True) replaces it. Backs up; returns change count."""
    data = vdf.read_bytes()
    parsed = list(read_entries(vdf))
    if not parsed:
        return 0
    results = []
    for fields, start, end in parsed:
        raw = data[start:end]
        r = fn(fields, start, raw)
        if r is None:
            results.append((None, True))
        elif isinstance(r, tuple):
            results.append((r[0], True))
        else:
            results.append((raw, False))
    if not any(modified for _, modified in results):
        return 0
    out = bytearray(data[: parsed[0][1]])
    i = 0
    for kept, _ in results:
        if kept is None:
            continue  # dropped entry
        # renumber the entry's index key, keep the rest of its raw bytes
        body = kept.index(0, 1) + 1
        out += b"\x00" + str(i).encode() + b"\x00" + kept[body:]
        i += 1
    out += data[parsed[-1][2] :]  # trailer after the last entry
    _rewrite(vdf, bytes(out))
    return sum(1 for _, modified in results if modified)


def _matches(fields: dict, title=None, path=None) -> bool:
    if title is not None and fields.get("AppName") == title:
        return True
    if path is not None:
        exe = fields.get("Exe", "").strip('"')
        return fields.get("ShortcutPath") == path or exe == path or path in exe
    return False


def find_appids(title=None, path=None) -> list[int]:
    return [f["appid"] for vdf in vdf_paths() for f, *_ in read_entries(vdf)
            if f.get("appid") and _matches(f, title, path)]


def in_app_dir(fields: dict) -> bool:
    sp = fields.get("ShortcutPath", "")
    exe = fields.get("Exe", "").strip('"')
    return sp.startswith(str(APP_DIR)) or exe.startswith(str(APP_DIR))


def imported_entries() -> list[tuple[str, int | None, str]]:
    """(slug, appid, title) of shortcuts referencing launcher files."""
    return [
        (os.path.basename(f.get("ShortcutPath") or f.get("Exe", "")).rsplit(".", 1)[0],
         f.get("appid"), f.get("AppName", ""))
        for vdf in vdf_paths()
        for f, *_ in read_entries(vdf)
        if in_app_dir(f)
    ]


def all_titles() -> set[str]:
    return {f.get("AppName") for vdf in vdf_paths()
            for f, *_ in read_entries(vdf) if f.get("AppName")}


def remove_matching(vdf: Path, title=None, path=None) -> list[int]:
    """Remove matching entries from one vdf; returns the removed appids."""
    removed: list[int] = []

    def fn(fields, start, raw):
        appid = fields.get("appid")
        if _matches(fields, title, path):
            if appid:
                removed.append(appid)
            return None
        return raw

    edit_entries(vdf, fn)
    return removed


def set_icon_fields(fallback=None) -> bool:
    """Fill empty icon fields (Steam closed; Steam shows shortcut icons only
    via this field): grid <appid>_icon.png, else fallback(fields) -> Path."""
    changed = False
    marker = b"\x01icon\x00"
    for vdf in vdf_paths():
        grid = vdf.parent / "grid"
        if not grid.is_dir():
            continue

        def fn(fields, start, raw):
            appid = fields.get("appid")
            icon = grid / f"{appid}_icon.png" if appid else None
            src = icon if icon and icon.is_file() else (fallback(fields) if fallback else None)
            if not src or fields.get("icon") or marker not in raw:
                return raw
            vstart = raw.find(marker) + len(marker)
            vstop = raw.index(0, vstart)
            return raw[:vstart] + str(src).encode() + raw[vstop:], True

        if edit_entries(vdf, fn):
            changed = True
    if changed:
        log("icon fields updated in shortcuts.vdf")
    return changed


def remove_grid_art(appids: list[int]) -> None:
    """Delete grid art belonging to appids across every user's grid dir."""
    for vdf in vdf_paths():
        grid = vdf.parent / "grid"
        if not grid.is_dir():
            continue
        for f in os.listdir(grid):
            if any(f == str(a) or f.startswith(p) for a in appids
                   for p in (f"{a}.", f"{a}_", f"{a}p.", f"{a}h.")):
                (grid / f).unlink(missing_ok=True)


def steam_running() -> bool:
    return any(subprocess.run(["pgrep", "-x", p], capture_output=True).returncode == 0
               for p in ("steam", "steamwebhelper"))


def steam_binary() -> str:
    return shutil.which("steam") or die("steam not found in PATH")


_steam_started = False


def ensure_steam() -> None:
    global _steam_started
    if not steam_running():
        if not _steam_started:
            _steam_started = True
            log("Steam is not running; starting it...")
            subprocess.Popen([steam_binary()], start_new_session=True)
        for _ in range(90):
            if steam_running():
                break
            time.sleep(2)
        else:
            die("Steam did not come up in time")


def add_to_steam(desktop: Path) -> None:
    SENTINEL.touch()
    url = f"steam://addnonsteamgame/{quote(str(desktop), safe='')}"
    try:
        subprocess.run([steam_binary(), url], check=True, timeout=30)
    except subprocess.TimeoutExpired:
        die(f"steam did not accept the URL within 30s ({desktop.name})")


def wait_for_appid(wrapper: Path, timeout: float = 30.0) -> int | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        ids = find_appids(path=str(wrapper))
        if ids:
            return ids[0]
        time.sleep(2)
    return None


def first_grid_dir() -> Path | None:
    """Grid dir of the first user config, if any shortcuts.vdf exists."""
    vdfs = list(vdf_paths())
    return vdfs[0].parent / "grid" if vdfs else None
