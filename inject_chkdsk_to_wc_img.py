#!/usr/bin/env python3
"""Inject CHKDSK.WMF into a Wild Commander wc.img (FAT32) and register it
in WC/wc.ini [PLUGINS].

FAT32 image logic reused from the Zuma VDAC2 injector (proven on host).
Self-contained so the ChkDsk project has no cross-project dependency.

Usage:
    python inject_chkdsk_to_wc_img.py [--img PATH] [--wmf PATH]

By default operates in-place on --img.
"""
from __future__ import annotations

import argparse
import struct
from pathlib import Path

ATTR_READ_ONLY = 0x01
ATTR_DIRECTORY = 0x10
ATTR_ARCHIVE = 0x20
ATTR_LFN = 0x0F
EOC = 0x0FFFFFFF

PLUGIN_NAME = "CHKDSK.WMF"


def le16(buf, off): return struct.unpack_from("<H", buf, off)[0]
def le32(buf, off): return struct.unpack_from("<I", buf, off)[0]
def put16(buf, off, v): struct.pack_into("<H", buf, off, v & 0xFFFF)
def put32(buf, off, v): struct.pack_into("<I", buf, off, v & 0xFFFFFFFF)


def short_checksum(short_name: bytes) -> int:
    chk = 0
    for b in short_name:
        chk = (((chk & 1) << 7) + (chk >> 1) + b) & 0xFF
    return chk


def sanitize_short(stem: str, ext: str = "") -> bytes:
    allowed = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_$~!#%&-{}()@'`"
    stem = "".join(ch for ch in stem.upper() if ch in allowed) or "FILE"
    ext = "".join(ch for ch in ext.upper() if ch in allowed)
    return stem[:8].ljust(8).encode("ascii") + ext[:3].ljust(3).encode("ascii")


def fits_83(name: str) -> bool:
    if name in (".", ".."):
        return True
    if name.count(".") > 1:
        return False
    if "." in name:
        stem, ext = name.rsplit(".", 1)
    else:
        stem, ext = name, ""
    return (
        1 <= len(stem) <= 8
        and len(ext) <= 3
        and sanitize_short(stem, ext).decode("ascii").rstrip()
        == (stem.upper().ljust(8) + ext.upper().ljust(3))
    )


class Fat32Image:
    def __init__(self, path: Path):
        self.path = path
        self.data = bytearray(path.read_bytes())
        self.bps = le16(self.data, 11)
        self.spc = self.data[13]
        self.reserved = le16(self.data, 14)
        self.fats = self.data[16]
        self.total_sectors = le32(self.data, 32)
        self.fat_size = le32(self.data, 36)
        self.root_cluster = le32(self.data, 44)
        self.first_data_sector = self.reserved + self.fats * self.fat_size
        self.cluster_size = self.bps * self.spc
        if self.bps != 512 or self.spc == 0 or self.fat_size == 0:
            raise RuntimeError("unsupported/non-FAT32 image")

    def cluster_offset(self, c): return (self.first_data_sector + (c - 2) * self.spc) * self.bps
    def fat_offset(self, c, fat=0): return (self.reserved + fat * self.fat_size) * self.bps + c * 4
    def get_fat(self, c): return le32(self.data, self.fat_offset(c)) & 0x0FFFFFFF

    def set_fat(self, c, value):
        for fat in range(self.fats):
            put32(self.data, self.fat_offset(c, fat), value)

    def cluster_chain(self, start):
        chain, cur = [], start
        while 2 <= cur < 0x0FFFFFF8:
            chain.append(cur)
            nxt = self.get_fat(cur)
            if nxt >= 0x0FFFFFF8:
                break
            cur = nxt
        return chain

    def read_chain(self, start):
        return b"".join(
            bytes(self.data[self.cluster_offset(c):self.cluster_offset(c) + self.cluster_size])
            for c in self.cluster_chain(start)
        )

    def allocate_clusters(self, count):
        max_clusters = (self.total_sectors - self.first_data_sector) // self.spc + 2
        found = []
        for c in range(2, max_clusters):
            if self.get_fat(c) == 0:
                found.append(c)
                if len(found) == count:
                    break
        if len(found) != count:
            raise RuntimeError("not enough free clusters")
        for idx, c in enumerate(found):
            self.set_fat(c, found[idx + 1] if idx + 1 < len(found) else EOC)
            off = self.cluster_offset(c)
            self.data[off:off + self.cluster_size] = b"\x00" * self.cluster_size
        return found

    def parse_dir(self, cluster):
        raw = self.read_chain(cluster)
        entries, lfn_parts, lfn_start_index = [], [], 0
        for idx in range(0, len(raw), 32):
            ent = raw[idx:idx + 32]
            first = ent[0]
            if first == 0x00:
                break
            if first == 0xE5:
                lfn_parts = []
                continue
            attr = ent[11]
            if attr == ATTR_LFN:
                if not lfn_parts:
                    lfn_start_index = idx // 32
                lfn_parts.append(ent)
                continue
            short = ent[:11]
            long_name = None
            if lfn_parts:
                pieces = []
                for lp in reversed(lfn_parts):
                    for pos in [1, 3, 5, 7, 9, 14, 16, 18, 20, 22, 24, 28, 30]:
                        code = struct.unpack_from("<H", lp, pos)[0]
                        if code == 0:
                            break
                        if code != 0xFFFF:
                            pieces.append(chr(code))
                long_name = "".join(pieces)
            entries.append({
                "index": idx // 32,
                "lfn_start": lfn_start_index if lfn_parts else idx // 32,
                "entries": (idx // 32) - (lfn_start_index if lfn_parts else idx // 32) + 1,
                "short": short,
                "name": long_name or self.short_to_name(short),
                "attr": attr,
                "cluster": (le16(bytearray(ent), 20) << 16) | le16(bytearray(ent), 26),
                "size": le32(bytearray(ent), 28),
            })
            lfn_parts = []
        return entries

    @staticmethod
    def short_to_name(short):
        stem = short[:8].decode("ascii", errors="ignore").rstrip()
        ext = short[8:11].decode("ascii", errors="ignore").rstrip()
        return f"{stem}.{ext}" if ext else stem

    def find_entry(self, dir_cluster, name):
        target = name.upper()
        for ent in self.parse_dir(dir_cluster):
            if ent["name"].upper() == target or self.short_to_name(ent["short"]).upper() == target:
                return ent
        return None

    def mark_deleted(self, dir_cluster, entry):
        for i in range(entry["entries"]):
            self.write_dir_byte(dir_cluster, (entry["lfn_start"] + i) * 32, 0xE5)

    def free_chain(self, start):
        if start < 2:
            return
        for cluster in self.cluster_chain(start):
            self.set_fat(cluster, 0)

    def write_dir_byte(self, dir_cluster, rel_off, value):
        chain = self.cluster_chain(dir_cluster)
        off = self.cluster_offset(chain[rel_off // self.cluster_size]) + rel_off % self.cluster_size
        self.data[off] = value

    def find_free_dir_slots(self, dir_cluster, needed):
        chain = self.cluster_chain(dir_cluster)
        while True:
            raw = self.read_chain(dir_cluster)
            run, start = 0, 0
            for idx in range(0, len(raw), 32):
                if raw[idx] in (0x00, 0xE5):
                    if run == 0:
                        start = idx // 32
                    run += 1
                    if run >= needed:
                        return start
                else:
                    run = 0
            new_cluster = self.allocate_clusters(1)[0]
            self.set_fat(chain[-1], new_cluster)
            self.set_fat(new_cluster, EOC)
            chain.append(new_cluster)

    def write_dir_entries(self, dir_cluster, slot, entries):
        chain = self.cluster_chain(dir_cluster)
        rel = slot * 32
        for ent in entries:
            off = self.cluster_offset(chain[rel // self.cluster_size]) + rel % self.cluster_size
            self.data[off:off + 32] = ent
            rel += 32

    def used_short_names(self, dir_cluster):
        return {ent["short"] for ent in self.parse_dir(dir_cluster)}

    def make_short_entry(self, short_name, attr, first_cluster, size):
        ent = bytearray(32)
        ent[:11] = short_name
        ent[11] = attr
        put16(ent, 20, (first_cluster >> 16) & 0xFFFF)
        put16(ent, 26, first_cluster & 0xFFFF)
        put32(ent, 28, size)
        return bytes(ent)

    def write_file_bytes(self, dir_cluster, name, payload: bytes):
        existing = self.find_entry(dir_cluster, name)
        if existing:
            self.free_chain(existing["cluster"])
            self.mark_deleted(dir_cluster, existing)
        clusters_needed = max(1, (len(payload) + self.cluster_size - 1) // self.cluster_size)
        clusters = self.allocate_clusters(clusters_needed)
        pos = 0
        for c in clusters:
            off = self.cluster_offset(c)
            chunk = payload[pos:pos + self.cluster_size]
            self.data[off:off + len(chunk)] = chunk
            pos += len(chunk)
        if not fits_83(name):
            raise RuntimeError(f"name {name!r} is not 8.3 (LFN not needed here)")
        if "." in name:
            stem, ext = name.rsplit(".", 1)
        else:
            stem, ext = name, ""
        short = sanitize_short(stem, ext)
        entries = [self.make_short_entry(short, ATTR_ARCHIVE, clusters[0], len(payload))]
        slot = self.find_free_dir_slots(dir_cluster, len(entries))
        self.write_dir_entries(dir_cluster, slot, entries)

    def read_file(self, dir_cluster, name) -> bytes:
        ent = self.find_entry(dir_cluster, name)
        if not ent:
            raise FileNotFoundError(name)
        return self.read_chain(ent["cluster"])[:ent["size"]]

    def save(self):
        self.path.write_bytes(self.data)


def register_in_ini(image: Fat32Image, wc_cluster: int) -> str:
    """Add CHKDSK.WMF to [PLUGINS] in WC/wc.ini, preserving bare-CR endings."""
    ini = image.read_file(wc_cluster, "wc.ini")
    if PLUGIN_NAME.encode("ascii") in ini:
        return "already registered"
    eol = b"\r" if (b"\r" in ini and b"\n" not in ini) else (b"\r\n" if b"\r\n" in ini else b"\n")
    marker = b"[PLUGINS]" + eol
    i = ini.find(marker)
    if i < 0:
        raise RuntimeError("[PLUGINS] section not found in wc.ini")
    insert_at = i + len(marker)
    ini = ini[:insert_at] + PLUGIN_NAME.encode("ascii") + eol + ini[insert_at:]
    image.write_file_bytes(wc_cluster, "wc.ini", ini)
    return "added to [PLUGINS]"


def main() -> int:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser()
    ap.add_argument("--img", default=r"C:\Users\Администратор\Desktop\Unreal\wc.img",
                    help="wc.img path (modified in place)")
    ap.add_argument("--wmf", default=str(here / "src" / "CHKDSK.WMF"),
                    help="built plugin to inject")
    args = ap.parse_args()

    img = Path(args.img)
    wmf = Path(args.wmf)
    if not img.exists():
        print(f"ERROR: image not found: {img}")
        return 1
    if not wmf.exists():
        print(f"ERROR: plugin not built: {wmf}  (run build.bat first)")
        return 1

    image = Fat32Image(img)
    wc = image.find_entry(image.root_cluster, "WC")
    if not wc or not (wc["attr"] & ATTR_DIRECTORY):
        print("ERROR: root \\WC folder not found in image (is this a Wild Commander disk?)")
        return 1
    wc_cluster = wc["cluster"]

    image.write_file_bytes(wc_cluster, PLUGIN_NAME, wmf.read_bytes())
    status = register_in_ini(image, wc_cluster)
    image.save()

    print(f"image : {img}")
    print(f"plugin: \\WC\\{PLUGIN_NAME}  ({wmf.stat().st_size} bytes)")
    print(f"wc.ini: {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
