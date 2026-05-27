#!/usr/bin/env python3
"""Одинаково испортить media-дескриптор ОБЕИХ копий FAT — для проверки детекта
ChkDsk «обе копии заголовка FAT битые».

Ставит младший байт FAT0[0] и FAT1[0] в 0x55 (!= media-байту BPB). Обе копии не
проходят media_ok И при этом остаются идентичными → backup-FAT mismatches = 0, но
ChkDsk сообщает «BOTH FAT headers BAD - unrecoverable» (зеркальный ремонт не помогает
— нет хорошей копии-источника). Это by-design невосстановимо зеркалом; `--restore`
возвращает корректный дескриптор (media-байт из BPB).

Запуск:
    python make_fat_both_bad.py [--img PATH]            # испортить оба заголовка FAT
    python make_fat_both_bad.py [--img PATH] --restore  # вернуть корректный дескриптор
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

    media = fi.data[21]                      # media-дескриптор BPB (корректный младший байт FAT[0])
    lowbyte = media if args.restore else 0x55
    for fat in (0, 1):
        off = fi.fat_offset(0, fat=fat)      # FAT[fat] запись 0, младший байт = media-дескриптор
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
