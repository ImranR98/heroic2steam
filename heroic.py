"""Heroic launcher side: config paths, sideload library, launch configs and
wrapper scripts pointing a game at its Proton/prefix."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from steam import APP_DIR, STEAM_DIR

HEROIC_CONFIG = Path(
    os.environ.get(
        "HEROIC_CONFIG",
        Path.home() / ".var/app/com.heroicgameslauncher.hgl/config/heroic",
    )
)
SIDELOAD_LIB = HEROIC_CONFIG / "sideload_apps/library.json"
STORE_CONFIG = HEROIC_CONFIG / "store/config.json"
GAMES_CONFIG_DIR = HEROIC_CONFIG / "GamesConfig"
ICONS_DIR = HEROIC_CONFIG / "icons"
IMAGES_CACHE = HEROIC_CONFIG / "images-cache"


def slug(title: str) -> str:
    """Lowercase, collapse non-alphanumeric runs to single hyphens."""
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def hidden_games() -> set[str]:
    """App names hidden in the Heroic library (store config games.hidden)."""
    try:
        cfg = json.loads(STORE_CONFIG.read_text())
        return {h["appName"] for h in cfg["games"]["hidden"]}
    except (OSError, ValueError, KeyError, TypeError):
        return set()


def sideload_games() -> list[dict]:
    lib = json.loads(SIDELOAD_LIB.read_text())
    games = lib.get("games", lib) if isinstance(lib, dict) else lib
    return [{
        "app": g["app_name"],
        "title": g.get("title") or g["app_name"],
        "exe": (g.get("install") or {}).get("executable", ""),
        "art_cover": g.get("art_cover", ""),
        "art_square": g.get("art_square", ""),
    } for g in games if g.get("is_installed", True)]


def game_launch(app: str) -> dict | None:
    """Proton binary + prefix a game launches with, from its GamesConfig."""
    cfg_file = GAMES_CONFIG_DIR / f"{app}.json"
    if not cfg_file.exists():
        return None
    cfg = json.loads(cfg_file.read_text()).get(app, {})
    wv = cfg.get("wineVersion") or {}
    if not (wv.get("bin") and cfg.get("winePrefix")):
        return None
    return {"proton": wv["bin"], "type": wv.get("type", "proton"), "prefix": cfg["winePrefix"]}


def write_launcher(title: str, exe: str, launch: dict) -> Path:
    """Write a .sh wrapper + .desktop for a game; returns the .desktop path."""
    APP_DIR.mkdir(parents=True, exist_ok=True)
    name = slug(title)
    wrapper = APP_DIR / f"{name}.sh"
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        f'export STEAM_COMPAT_DATA_PATH="{launch["prefix"]}"\n'
        f'export STEAM_COMPAT_CLIENT_INSTALL_PATH="{STEAM_DIR}"\n'
        f'cd "{os.path.dirname(exe)}"\n'
        + (
            f'exec "{launch["proton"]}" run "{exe}"\n'
            if launch["type"] != "wine"
            else f'export WINEPREFIX="{launch["prefix"]}"\n'
            f'exec "{launch["proton"]}" "{exe}"\n'
        )
    )
    wrapper.chmod(0o755)
    desktop = APP_DIR / f"{name}.desktop"
    desktop.write_text(
        "[Desktop Entry]\nType=Application\n"
        f"Name={title}\nExec={wrapper}\n"
        "NoDisplay=true\nHidden=true\nTerminal=false\n"
    )
    return desktop
