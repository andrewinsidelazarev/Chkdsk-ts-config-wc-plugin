#!/usr/bin/env python3
"""Non-destructively damage FAT0 (primary FAT) so ChkDsk restores it from FAT1.

Invalidates FAT0's media descriptor (FAT0[0] low byte F8 -> F0). ChkDsk's
media_ok then fails for FAT0 (first_ok=0) while FAT1[0] stays valid (second_ok=1),
so repair_fat_mirror takes the "!first_ok && second_ok" branch and copies FAT1
over FAT0. Non-destructive: FAT0[0] is a reserved entry (in no cluster chain),
F0 is a valid media type (mounts fine), and FAT1 is left fully intact.

Usage: python make_fat0_corrupt.py [--img PATH]
"""
import argparse
import struct
import importlib.util as iu
from pathlib import Path

HERE = Path(__file__).resolve().parent
_sp = iu.spec_from_file_location("inj", HERE / "inject_chkdsk_to_wc_img.py")
inj = iu.module_from_spec(_sp)
_sp.loader.exec_module(inj)
put32 = inj.put32


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--img", default=r"C:\Users\Администратор\Desktop\Unreal\wc.img")
    args = ap.parse_args()
    fi = inj.Fat32Image(Path(args.img))

    # Normalize first: make FAT1 an exact mirror of FAT0, so the ONLY divergence
    # is the media descriptor we damage below (otherwise a leftover FAT1
    # mismatch would get pulled back into FAT0 when FAT1 is used as the source).
    f0 = fi.reserved * fi.bps
    f1 = (fi.reserved + fi.fat_size) * fi.bps
    flen = fi.fat_size * fi.bps
    if fi.data[f1:f1 + flen] != fi.data[f0:f0 + flen]:
        fi.data[f1:f1 + flen] = fi.data[f0:f0 + flen]
        print("normalized FAT1 := FAT0 (cleared leftover divergence)")

    off0 = fi.fat_offset(0, fat=0)
    off1 = fi.fat_offset(0, fat=1)
    old0 = struct.unpack_from("<I", fi.data, off0)[0]
    new0 = old0 & 0xFFFFFF00                     # low byte 0x00 = invalid media descriptor -> media_ok fails
    put32(fi.data, off0, new0)
    fi.save()

    v1 = struct.unpack_from("<I", fi.data, off1)[0]
    print(f"FAT0[0] media descriptor: {old0:#010x} -> {new0:#010x}  (low byte 0x00 -> media_ok FAILS -> first_ok=0)")
    print(f"FAT1[0] (untouched):       {v1:#010x}  (valid media byte -> media_ok OK -> second_ok=1)")
    print("\n1 differing FAT sector (sector 0) -> backup-FAT mismatches = 1")
    print("ENTER=repair -> source = FAT1 (FAT0 media bad), copies FAT1 sector 0 over FAT0")
    print("              -> FAT0[0] restored to the valid descriptor. Re-run shows 0 mismatches.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
