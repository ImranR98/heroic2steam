# heroic2steam

Import Heroic Games Launcher **sideload** (non-Steam) games into Steam on Linux, reusing the game's Heroic Proton/wine binary and prefix, and installing Steam grid art (portrait, hero, capsule, icon). `sync` also removes imported shortcuts that no longer exist in Heroic.

## How it works

For each game it finds in Heroic's sideload library:

1. Reads the game's `GamesConfig/<app>.json` for the wine/Proton binary and prefix.
2. Writes a small launcher pair into `~/.local/share/applications/steam-add-nonsteam-game/`:
   - `<slug>.sh` – bash wrapper exporting `STEAM_COMPAT_DATA_PATH` (prefix) and `STEAM_COMPAT_CLIENT_INSTALL_PATH`, then `exec`s `proton run <exe>`.
   - `<slug>.desktop` – `NoDisplay` desktop entry pointing at the wrapper.
3. Starts Steam if needed and opens `steam://addnonsteamgame/<desktop>`.
4. Watches the binary `shortcuts.vdf` (under each `userdata/*/config/`) for the new entry, reads back its `appid`, and writes grid art as `grid/<appid>_*` images sourced from Heroic icons, cached covers, and icons embedded in the game's `.exe`.

`shortcuts.vdf` is edited directly (with a `.bak` backup) when removing games — Steam must be **closed** for that.

## Usage

```
python3 main.py [add] TITLE...|all [--dry-run]
python3 main.py remove TITLE...
python3 main.py sync [--dry-run]
```

- **add** (default): `all` imports every installed sideload game; otherwise partial matches on the title are imported. `--dry-run`/`-n` only reports what would happen.
- **remove**: removes the launcher, the Steam shortcut(s), and their grid art. Steam must be closed.
- **sync**: adds games not yet in Steam (skipping games hidden in the Heroic library), removes imported shortcuts that no longer exist in Heroic, and — when run with Steam closed — fills each shortcut's `icon` field so Steam actually displays the icons. Steam shows shortcut icons only via that field, so run sync once with Steam closed after importing for icons to appear.

## Configuration

| Variable | Default |
|---|---|
| `HEROIC_CONFIG` | `~/.var/app/com.heroicgameslauncher.hgl/config/heroic` (flatpak) |
| `STEAM_DIR` | `~/.local/share/Steam` |

Other paths used: `sideload_apps/library.json`, `GamesConfig/`, `icons/`, `images-cache/` under `HEROIC_CONFIG`; launchers in `~/.local/share/applications/steam-add-nonsteam-game/`.

Requires Python 3.10+. Pillow (`pip install pillow`) is optional but needed for grid art.

## Layout

- `main.py` – CLI and add/remove/sync commands
- `steam.py` – Steam side: `shortcuts.vdf` parsing/editing, process control, grid cleanup
- `heroic.py` – Heroic side: config paths, sideload library, launcher writing
- `art.py` – grid art: cover sources, embedded `.exe` icon extraction (pure-PE, no Pillow needed for extraction itself)
