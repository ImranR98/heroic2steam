"""Grid art: pull Heroic covers, extract Windows exe icons, and write Steam
grid images (portrait/hero/capsule/icon) for an imported appid."""

from __future__ import annotations

import hashlib
import io
import struct
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    Image = None

from heroic import ICONS_DIR, IMAGES_CACHE
from steam import first_grid_dir, log


def cache_path(url: str) -> Path | None:
    if not url:
        return None
    f = IMAGES_CACHE / hashlib.sha256(url.encode()).hexdigest()
    return f if f.exists() else None


def portrait_source(app: str, art_square: str) -> Path | None:
    p = ICONS_DIR / f"{app}.jpg"
    return p if p.is_file() else cache_path(art_square)


def extract_exe_icon(exe: str):
    """Largest icon embedded in a Windows exe as a PIL image (PE resources)."""
    if Image is None:
        return None
    try:
        data = Path(exe).read_bytes()
    except OSError:
        return None
    if len(data) < 0x40 or data[:2] != b"MZ":
        return None
    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    if data[e_lfanew : e_lfanew + 4] != b"PE\0\0":
        return None
    coff = e_lfanew + 4
    nsec = struct.unpack_from("<H", data, coff + 2)[0]
    opt = coff + 20
    opt_size = struct.unpack_from("<H", data, coff + 16)[0]
    dd = opt + (96 if struct.unpack_from("<H", data, opt)[0] == 0x10B else 112)
    res_rva, _ = struct.unpack_from("<II", data, dd + 16)  # resource directory
    if not res_rva:
        return None
    secs = []
    for i in range(nsec):
        s = coff + 20 + opt_size + i * 40
        vsize, vaddr, rsize, rptr = struct.unpack_from("<IIII", data, s + 8)
        secs.append((vaddr, max(vsize, rsize), rptr))

    def off(rva):
        return next((rptr + (rva - vaddr) for vaddr, vsize, rptr in secs
                     if vaddr <= rva < vaddr + vsize), None)

    base = off(res_rva)
    if base is None:
        return None
    def u16(o):
        return (struct.unpack_from("<H", data, o)[0]
                if 0 <= o + 2 <= len(data) else None)

    def u32(o):
        return (struct.unpack_from("<I", data, o)[0]
                if 0 <= o + 4 <= len(data) else None)

    def walk(o, depth=0):  # resource dir tree -> {id: {id: (file_off, size)}}
        out = {}
        if depth > 8 or o is None:
            return out
        n, m = u16(o + 12), u16(o + 14)
        if n is None or m is None or n + m > 1024:  # garbage count -> bail
            return out
        e = o + 16
        for _ in range(n + m):
            nid, doff = u32(e), u32(e + 4)
            if nid is None or doff is None:
                break
            e += 8
            if doff & 0x80000000:
                out[nid & 0x7FFFFFFF] = walk(base + (doff & 0x7FFFFFFF), depth + 1)
            else:
                rva, size = u32(base + doff), u32(base + doff + 4)
                out[nid & 0x7FFFFFFF] = (off(rva) if rva is not None else None,
                                         size if size is not None else 0)
        return out

    leaves = []
    node = walk(base).get(3)  # RT_ICON

    def collect(n):
        for v in n.values():
            collect(v) if isinstance(v, dict) else leaves.append(v)

    if isinstance(node, dict):
        collect(node)
    blobs = sorted(
        (data[o : o + size] for o, size in leaves if o is not None),
        key=len,
        reverse=True,
    )

    def decode_dib(blob: bytes):
        """Icon DIB (1/4/8/24/32bpp) to RGBA via a built BMP + AND mask."""
        if len(blob) < 40:
            return None
        w = struct.unpack_from("<i", blob, 4)[0]
        h = struct.unpack_from("<i", blob, 8)[0]
        bpp = struct.unpack_from("<H", blob, 14)[0]
        if w <= 0 or w > 1024 or bpp not in (1, 4, 8, 24, 32):
            return None
        img_h = abs(h) // 2 if h > 0 else abs(h)  # icon DIBs double the height
        if not img_h:
            return None
        clr = struct.unpack_from("<I", blob, 32)[0] or (0 if bpp >= 24 else 1 << bpp)
        stride = ((w * bpp + 31) // 32) * 4
        xor_len = stride * img_h
        pal = 40 + clr * 4
        if pal + xor_len > len(blob):
            return None
        header = struct.pack(
            "<IiiHHIIiiII", 40, w, img_h, 1, bpp, 0, xor_len, 0, 0, clr, 0
        )
        rows = blob[pal : pal + xor_len]  # XOR rows, bottom-up
        pix_off = 14 + 40 + clr * 4
        bmp = (
            b"BM"
            + struct.pack("<IHHI", pix_off + len(rows), 0, 0, pix_off)
            + header
            + blob[40:pal]  # palette (empty for bpp >= 24)
            + rows
        )
        im = Image.open(io.BytesIO(bmp)).convert("RGBA")
        # AND mask (1 = transparent) trails the XOR rows
        mask = pal + xor_len
        if bpp < 32 and mask + ((w + 31) // 32) * 4 * img_h <= len(blob):
            px = im.load()
            mstride = ((w + 31) // 32) * 4
            for y in range(img_h):
                row = blob[mask + (img_h - 1 - y) * mstride :]
                for x in range(w):
                    if row[x >> 3] & (0x80 >> (x & 7)):
                        px[x, y] = (0, 0, 0, 0)
        return im

    for blob in blobs:
        try:
            im = (
                Image.open(io.BytesIO(blob)).convert("RGBA")
                if blob[:4] == b"\x89PNG"
                else decode_dib(blob)
            )
            if im:
                return im
        except Exception:
            continue
    return None


def install_art(game: dict, appid: int, exe: str) -> None:
    if Image is None:
        return
    grid = first_grid_dir()
    if grid is None:
        return
    grid.mkdir(parents=True, exist_ok=True)

    def save(im, suffix: str):  # never clobber existing art
        out = grid / f"{appid}{suffix}"
        if not out.exists():
            im.convert("RGB").save(out, "JPEG", quality=95)

    portrait = portrait_source(game["app"], game.get("art_square", ""))
    if portrait and portrait.is_file():
        im = Image.open(portrait)
        if im.size != (600, 900):
            im = im.resize((600, 900), Image.LANCZOS)
        save(im, "p.jpg")

    hero = cache_path(game.get("art_cover", ""))
    if hero:
        im = Image.open(hero).convert("RGB").resize((1920, 620), Image.LANCZOS)
        save(im, "_hero.jpg")
    elif portrait and portrait.is_file():
        im = Image.open(portrait).convert("RGB")
    else:
        im = None
    if im:
        w, h = im.size
        cw = int(h * 4 / 3)  # wide capsule = 4:3 center crop
        left = max(0, (w - cw) // 2)
        save(im.crop((left, 0, left + cw, h)).resize((600, 450), Image.LANCZOS), ".jpg")

    icon = extract_exe_icon(exe)
    if not icon and portrait and portrait.is_file():
        im = Image.open(portrait).convert("RGB")
        s = min(im.size)
        icon = im.crop(((im.size[0] - s) // 2, (im.size[1] - s) // 2,
                        (im.size[0] + s) // 2, (im.size[1] + s) // 2))
    if icon:
        # regenerate, not keep
        icon.resize((256, 256), Image.LANCZOS).convert("RGBA").save(grid / f"{appid}_icon.png", "PNG")
    log(f"art installed for {game['title']} (appid {appid})")
