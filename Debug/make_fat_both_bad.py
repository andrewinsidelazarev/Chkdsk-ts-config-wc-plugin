#!/usr/bin/env python3
"""Damage the media descriptor of BOTH FATs identically, to test ChkDsk's
"both FAT headers bad" detection.

Sets FAT0[0] and FAT1[0] low byte to 0x55 (!= BPB media). Both fail media_ok
AND the copies stay identical -> backup-FAT mismatches = 0, yet ChkDsk now
reports "BOTH FAT headers BAD - unrecoverable" (mirror repair cannot help -
there is no good copy to restore from). This is by design unrecoverable by
ChkDsk; use --restore to put the valid descriptor (BPB media byte) back.

Usage:
    python make_fat_both_bad.py [--img PATH]            # damage both FAT headers
    python make_fat_both_bad.py [--img PATH] --restore  # restore valid descriptor
"""
import argparse
import struct
import importlib.util as iu
from pathlib import Path

HERE = Path(__file__).resolve().parent
_sp = iu.spec_from_file_location("inj", HERE.parent / "inject_chkdsk_to_wc_img.py")
inj = iu.module_from_spec(_sp)
_sp.loader.exec_module(inj)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--img", default=r"C:\Users\Администратор\Desktop\Unreal\wc.img")
    ap.add_argument("--restore", action="store_true", help="put the valid media descriptor back")
    args = ap.parse_args()
    fi = inj.Fat32Image(Path(args.img))

    media = fi.data[21]                      # BPB media descriptor (the correct FAT[0] low byte)
    lowbyte = media if args.restore else 0x55
    for fat in (0, 1):
        off = fi.fat_offset(0, fat=fat)      # FAT[fat] entry 0, low byte = media descriptor
        fi.data[off] = lowbyte
    fi.save()

    if args.restore:
        print(f"restored FAT0[0] & FAT1[0] low byte -> {media:#04x} (BPB media descriptor) - headers valid again")
    else:
        print(f"FAT0[0] & FAT1[0] low byte -> 0x55 (BPB media is {media:#04x}) - both headers now FAIL media_ok")
        print("identical corruption -> backup-FAT mismatches = 0")
        print('ChkDsk should report: "BOTH FAT headers BAD - unrecoverable"  (ENTER cannot fix it)')
        print("restore with:  python make_fat_both_bad.py --restore")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
