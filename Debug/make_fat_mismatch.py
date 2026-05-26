#!/usr/bin/env python3
"""Corrupt FAT1 (the backup FAT) in a few sectors to test ChkDsk's FAT-mirror repair.

Writes wrong entries into FAT1 ONLY (FAT0 untouched) -> N differing FAT sectors
-> ChkDsk reports N "backup-FAT mismatches", and ENTER=repair copies FAT0 over
FAT1 (FAT0[0] media descriptor stays valid -> FAT0 is the chosen good source).

ChkDsk counts mismatches PER SECTOR (cmp_fat_mirror), so each corrupted sector
adds exactly 1. We touch one entry in each of N distinct FAT sectors.

Usage: python make_fat_mismatch.py [--img PATH] [--sectors N]
"""
import argparse
import importlib.util as iu
from pathlib import Path

HERE = Path(__file__).resolve().parent
_sp = iu.spec_from_file_location("inj", HERE.parent / "inject_chkdsk_to_wc_img.py")
inj = iu.module_from_spec(_sp)
_sp.loader.exec_module(inj)
put32 = inj.put32

ENTRIES_PER_SECTOR = 128                  # 512 bytes / 4 bytes per FAT32 entry


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--img", default=r"C:\Users\Администратор\Desktop\Unreal\wc.img")
    ap.add_argument("--sectors", type=int, default=5, help="number of FAT1 sectors to corrupt")
    args = ap.parse_args()

    fi = inj.Fat32Image(Path(args.img))
    done = []
    for k in range(1, args.sectors + 1):
        c = ENTRIES_PER_SECTOR * k + 7    # cluster in a distinct FAT sector (skip sector 0 = media desc)
        fat0 = fi.get_fat(c)              # FAT0 value (left untouched)
        bad = 0x0FFFFFF7 if (fat0 & 0x0FFFFFFF) != 0x0FFFFFF7 else 0x00000001
        put32(fi.data, fi.fat_offset(c, fat=1), bad)   # write FAT1 entry ONLY -> diverges from FAT0
        done.append((c, c // ENTRIES_PER_SECTOR, fat0, bad))
    fi.save()

    print(f"corrupted {len(done)} FAT1 sector(s) (FAT0 untouched, FAT0[0] media descriptor still valid):")
    for c, sec, f0, bad in done:
        print(f"  cluster {c:>6}  FAT sector {sec:>4}:  FAT0={f0:#010x}  ->  FAT1={bad:#010x}")
    print(f"\nChkDsk should report  backup-FAT mismatches = {len(done)}")
    print("ENTER=repair -> repair_fat_mirror copies FAT0 over FAT1 (resolves to 0).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
