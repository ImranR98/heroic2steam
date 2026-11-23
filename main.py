#!/usr/bin/env python3
"""Import Heroic sideload games into Steam with their Heroic Proton/prefix and
art; add|remove|sync modes. Run with no args for usage."""

from __future__ import annotations

import sys

from art import install_art, portrait_source
from heroic import game_launch, hidden_games, sideload_games, slug, write_launcher
from steam import (
    APP_DIR,
    LEGACY_APP_DIR,
    add_to_steam,
    all_titles,
    die,
    ensure_steam,
    find_appids,
    imported_entries,
    legacy_launcher_slugs,
    log,
    migrate_launcher_paths,
    remove_grid_art,
    remove_matching,
    set_icon_fields,
    steam_running,
    vdf_paths,
    wait_for_appid,
)

USAGE = (
    "usage: import-heroic-games [add] TITLE...|all [--dry-run] [--regen]\n"
    "       import-heroic-games remove TITLE...\n"
    "       import-heroic-games sync [--dry-run] [--regen]"
)


def import_game(game: dict, dry_run: bool, force: bool = False) -> None:
    launch = game_launch(game["app"])
    if launch is None:
        log(f"skip {game['app']}: no GamesConfig")
        return
    if not game["exe"]:
        log(f"skip {game['app']}: no executable")
        return
    title, exe = game["title"], game["exe"]
    name = slug(title)
    wrapper = APP_DIR / f"{name}.sh"
    existing = (
        find_appids(title=title)
        or find_appids(path=str(wrapper))
        or find_appids(path=str(LEGACY_APP_DIR / f"{name}.sh"))
    )
    if existing:
        if force:
            log(f"{'would regenerate' if dry_run else 'regenerated'} launcher: {title}")
            if not dry_run:
                write_launcher(title, exe, launch)
                for appid in existing:
                    install_art(game, appid, exe)
        else:
            log(f"already in Steam: {title}")
            if not dry_run:
                for appid in existing:
                    install_art(game, appid, exe)
        return
    if dry_run:
        log(f"would add: {title} (proton={launch['proton']}, prefix={launch['prefix']})")
        return
    ensure_steam()
    add_to_steam(write_launcher(title, exe, launch))
    log(f"added to Steam: {title}")
    appid = wait_for_appid(wrapper)
    if appid:
        install_art(game, appid, exe)
    else:
        log(f"warning: could not read back appid for {title}; art not installed")


def remove_game(title: str) -> None:
    if steam_running():
        die("Steam must be closed to remove games (it rewrites shortcuts.vdf on exit)")
    name = slug(title)
    launchers = [
        (root / f"{name}.sh", root / f"{name}.desktop")
        for root in (APP_DIR, LEGACY_APP_DIR)
    ]
    removed: list[int] = []
    for vdf in vdf_paths():
        for wrapper, desktop in launchers:
            removed += remove_matching(vdf, path=str(wrapper))
            removed += remove_matching(vdf, path=str(desktop))
    if not removed:
        log(f"not found in Steam: {title}")
        return
    remove_grid_art(removed)
    for files in launchers:
        for f in files:
            f.unlink(missing_ok=True)
    log(f"removed from Steam: {title} (appid(s): {', '.join(map(str, removed))})")


def cmd_sync(dry_run: bool, regen: bool = False) -> None:
    running = steam_running()
    hidden = hidden_games()
    heroic = {slug(g["title"]): g for g in sideload_games()}
    imported = {s: (a, t) for s, a, t in imported_entries()}
    titles = all_titles()
    to_add = [s for s, g in heroic.items() if g["title"] not in titles]
    hidden_adds = [s for s in to_add if heroic[s]["app"] in hidden]
    to_add = [s for s in to_add if s not in hidden_adds]
    to_remove = [s for s in imported if s not in heroic]
    to_regen = [s for s in heroic if s in imported] if regen else []
    legacy = legacy_launcher_slugs()

    log(f"sync: {len(to_add)} to add, {len(to_remove)} to remove"
        + (f", {len(to_regen)} to regenerate" if regen else ""))
    for s in to_remove:
        log(f"  remove: {imported[s][1]}")
    for s in to_add:
        log(f"  add:    {heroic[s]['title']}")
    for s in to_regen:
        log(f"  regen:  {heroic[s]['title']}")
    for s in hidden_adds:
        log(f"  skip:   {heroic[s]['title']} (hidden in Heroic)")
    if legacy:
        if running:
            log(f"note: {len(legacy)} shortcut(s) still use the old launcher directory; "
                "close Steam and run sync once to move them")
        elif dry_run:
            log(f"would migrate {len(legacy)} shortcut(s) out of the applications directory")

    if dry_run:
        return

    migrated: set[str] = set()
    if legacy and not running:
        for s in sorted(legacy):
            game = heroic.get(s)
            if not game or (APP_DIR / f"{s}.sh").is_file():
                continue
            launch = game_launch(game["app"])
            if launch is None or not game["exe"]:
                log(f"skip migrating {game['title']}: no launcher config")
                continue
            write_launcher(game["title"], game["exe"], launch)
        migrated = migrate_launcher_paths()
        if migrated:
            log(f"moved {len(migrated)} shortcut(s) out of {LEGACY_APP_DIR}")
            to_regen = [s for s in to_regen if s not in migrated]

    if not running:
        # Drop old copies once a replacement launcher exists, including ones
        # left behind by an earlier migration.
        for s in heroic:
            if (APP_DIR / f"{s}.sh").is_file():
                for suffix in (".sh", ".desktop"):
                    (LEGACY_APP_DIR / f"{s}{suffix}").unlink(missing_ok=True)
        try:
            LEGACY_APP_DIR.rmdir()
        except OSError:
            pass

    if to_remove:
        if running:
            log(f"note: Steam is running; {len(to_remove)} orphan(s) will be "
                "removed on a later sync with Steam closed")
        else:
            for s in to_remove:
                remove_game(imported[s][1])
    if not running:
        games = list(heroic.values())

        def icon_fallback(fields):
            game = next((g for g in games if g["title"] == fields.get("AppName", "")), None)
            return portrait_source(game["app"], game.get("art_square", "")) if game else None

        set_icon_fields(icon_fallback)  # Steam closed: fill empty icon fields
    elif not dry_run:
        log("note: Steam is running; icon fields will be filled on a sync "
            "run with Steam closed")
    if to_add:
        ensure_steam()
        for s in to_add:
            import_game(heroic[s], dry_run=False, force=regen)
    for s in to_regen:
        if s not in to_add:
            import_game(heroic[s], dry_run=False, force=True)
    log("sync complete")


def main(argv: list[str]) -> int:
    if not argv:
        die(USAGE)
    mode = argv.pop(0) if argv[0] in ("add", "remove", "sync") else "add"
    dry_run = "--dry-run" in argv or "-n" in argv
    regen = "--regen" in argv or "--force" in argv or "-f" in argv
    argv = [a for a in argv if a not in
            ("--dry-run", "-n", "--regen", "--force", "-f")]

    if mode == "remove":
        if not argv:
            die("usage: import-heroic-games remove TITLE [...]")
        for title in argv:
            remove_game(title)
        return 0
    if mode == "sync":
        cmd_sync(dry_run, regen)
        return 0

    games = sideload_games()
    if not argv or "all" in argv:
        picked = games
    else:
        picked = []
        for w in argv:
            found = [g for g in games if w.lower() in g["title"].lower()]
            if found:
                picked.extend(found)
            else:
                log(f"no installed game matches: {w}")
    if not picked:
        die("nothing to import")
    log(f"importing {len(picked)} game(s)...")
    for game in picked:
        import_game(game, dry_run, force=regen)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
