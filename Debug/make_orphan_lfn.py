#!/usr/bin/env python3
"""Inject ORPHANED LFN entries into a FAT32 image — reproduces the Wild Commander
delete bug so ChkDsk's detect/repair can be exercised.

WC's CORE32 `DELEN` marks ONLY the short 8.3 entry as deleted (0xE5) and leaves the
preceding long-file-name (LFN, attr 0x0F) entries intact. Every file/dir with a long
name (all Cyrillic / mixed-case names) thus leaves a run of orphaned 0x0F entries
pointing at a now-deleted short entry — FAT32 directory corruption.

This tool recreates that state in a directory:
  * one VALID long-named file (LFN run + LIVE short entry) — ChkDsk must IGNORE it
    (checksum matches its owner), proving repair never deletes good long names;
  * several ORPHAN long-named files (LFN run + short entry then marked 0xE5),
    including one whose LFN run STRADDLES a 512-byte sector boundary (exercises the
    cross-sector tail fix). Files are zero-length (first cluster 0) so NO clusters
    are touched -> no lost clusters / FAT changes, isolating the orphan-LFN case.

The whole working region is pre-filled with 0xE5 (deleted) filler so runs can be
placed at chosen slots without a 0x00 (end-of-dir) gap cutting the scan short.

Usage (hardware image):  python make_orphan_lfn.py [--img PATH] [--dir NAME]
Importable: inject(fi, dir_cluster) -> dict(metadata) for the test harness.
"""
import argparse
import importlib.util as iu
from pathlib import Path

HERE = Path(__file__).resolve().parent
_sp = iu.spec_from_file_location("inj", HERE.parent / "inject_chkdsk_to_wc_img.py")
inj = iu.module_from_spec(_sp)
_sp.loader.exec_module(inj)

LFN_POS = [1, 3, 5, 7, 9, 14, 16, 18, 20, 22, 24, 28, 30]   # char slots in a 0x0F entry


def build_lfn_entries(long_name: str, checksum: int):
    """LFN entries for `long_name`, in on-disk order (highest sequence first)."""
    units = long_name.encode("utf-16-le")
    chars = [units[i:i + 2] for i in range(0, len(units), 2)]
    n = (len(chars) + 12) // 13                      # ceil(len/13) LFN entries
    out = []
    for seq in range(1, n + 1):
        e = bytearray(32)
        e[11] = 0x0F                                 # LFN attribute
        e[12] = 0x00
        e[13] = checksum                             # == short-name checksum (all entries)
        base = (seq - 1) * 13
        for k, pos in enumerate(LFN_POS):
            idx = base + k
            if idx < len(chars):
                e[pos:pos + 2] = chars[idx]
            elif idx == len(chars):
                e[pos:pos + 2] = b"\x00\x00"         # name terminator
            else:
                e[pos:pos + 2] = b"\xff\xff"         # padding
        e[0] = seq | (0x40 if seq == n else 0)       # 0x40 marks the last logical part
        out.append(bytes(e))
    out.reverse()                                    # on disk: seq N (0x40) first .. seq 1 last
    return out


def _name(n_lfn: int, i: int) -> str:
    """A name that needs exactly n_lfn LFN entries (length in ((n-1)*13, n*13])."""
    length = (n_lfn - 1) * 13 + 5
    return (f"orphan-file-{i:02d}-" + "x" * (n_lfn * 13)) [:length]


def inject(fi, dir_cluster):
    """Place a valid + several orphan long-named files into dir_cluster.
    Returns metadata: expected orphan-LFN entry count, byte offsets of every orphan
    LFN entry (must become 0xE5 after repair), and whether a straddle was produced.
    """
    cs = fi.cluster_size
    chain = fi.cluster_chain(dir_cluster)

    def slot_off(slot):                               # absolute image byte offset of a 32-byte slot
        rel = slot * 32
        return fi.cluster_offset(chain[rel // cs]) + rel % cs

    REGION = 48
    base = fi.find_free_dir_slots(dir_cluster, REGION + 1)   # +1 keeps a 0x00 terminator after
    chain = fi.cluster_chain(dir_cluster)                    # may have grown (auto-extend)
    for s in range(base, base + REGION):                     # pre-fill region with deleted filler
        fi.data[slot_off(s)] = 0xE5

    # layout: [valid 2-LFN][orphan 1-LFN][orphan 3-LFN][orphan 2-LFN STRADDLE][orphan 2-LFN]
    runs = []
    cur = base
    runs.append(("valid", 2, False)); cur += 3
    runs.append(("orph",  1, True));  cur += 2
    runs.append(("orph",  3, True));  cur += 4
    t = cur                                                   # straddle: start at next slot with idx%16==15
    while t % 16 != 15:
        t += 1
    straddle_at = t
    runs2 = [("valid", 2, False, base),
             ("orph", 1, True,  base + 3),
             ("orph", 3, True,  base + 5),
             ("orph", 2, True,  straddle_at),                 # LFN run crosses sector boundary
             ("orph", 2, True,  straddle_at + 3)]
    assert (straddle_at + 3 + 3) - base <= REGION, "region too small"

    orph_offsets, orph_slots, expected, idx = [], [], 0, 0
    for kind, n_lfn, deleted, slot in runs2:
        idx += 1
        nm = _name(n_lfn, idx)
        short = (f"ORP{idx:02d}".ljust(8) + "TMP").encode("ascii")[:11]
        chk = inj.short_checksum(short)
        lfns = build_lfn_entries(nm, chk)
        assert len(lfns) == n_lfn, (nm, len(lfns), n_lfn)
        short_ent = fi.make_short_entry(short, inj.ATTR_ARCHIVE, 0, 0)
        # write LFN entries then the short entry, contiguously
        for j, le in enumerate(lfns):
            fi.data[slot_off(slot + j):slot_off(slot + j) + 32] = le
        fi.data[slot_off(slot + n_lfn):slot_off(slot + n_lfn) + 32] = short_ent
        if deleted:
            fi.data[slot_off(slot + n_lfn)] = 0xE5            # WC bug: delete ONLY the short entry
            expected += n_lfn
            for j in range(n_lfn):
                orph_offsets.append(slot_off(slot + j))
                orph_slots.append(slot + j)

    straddled = (straddle_at % 16) == 15
    return {
        "expected": expected,
        "orph_offsets": orph_offsets,
        "orph_slots": orph_slots,
        "base": base,
        "straddle_slot": straddle_at,
        "straddled": straddled,
        "dir_cluster": dir_cluster,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--img", default=r"C:\Users\Администратор\Desktop\Unreal\wc.img")
    ap.add_argument("--dir", default="", help="subdir name to corrupt (default: root)")
    args = ap.parse_args()

    fi = inj.Fat32Image(Path(args.img))
    target = fi.root_cluster
    if args.dir:
        ent = fi.find_entry(fi.root_cluster, args.dir)
        if not ent or not (ent["attr"] & inj.ATTR_DIRECTORY):
            print(f"directory {args.dir!r} not found"); return 1
        target = ent["cluster"]

    meta = inject(fi, target)
    fi.save()
    print(f"injected orphaned LFN entries into cluster {target}")
    print(f"  orphan LFN entries (expected ChkDsk count): {meta['expected']}")
    print(f"  straddle run at slot {meta['straddle_slot']} (crosses sector boundary): {meta['straddled']}")
    print("  + 1 VALID long-named file (must NOT be touched by repair)")
    print("ChkDsk should report  orphaned LFN entries =", meta["expected"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
