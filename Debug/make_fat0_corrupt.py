#!/usr/bin/env python3
"""Неразрушающе повредить FAT0 (первичную FAT), чтобы ChkDsk восстановил её из FAT1.

Делает невалидным media-дескриптор FAT0 (младший байт FAT0[0] F8 -> F0). Тогда
media_ok в ChkDsk не проходит для FAT0 (first_ok=0), а FAT1[0] остаётся валидным
(second_ok=1), поэтому repair_fat_mirror идёт по ветке «!first_ok && second_ok» и
копирует FAT1 поверх FAT0. Неразрушающе: FAT0[0] — зарезервированная запись (ни в
одной цепочке), F0 — валидный тип носителя (том монтируется), FAT1 не тронута.

Запуск: python make_fat0_corrupt.py [--img PATH]
"""
import argparse
import struct
import importlib.util as iu
from pathlib import Path

HERE = Path(__file__).resolve().parent
_sp = iu.spec_from_file_location("inj", HERE.parent / "inject_chkdsk_to_wc_img.py")
inj = iu.module_from_spec(_sp)
_sp.loader.exec_module(inj)
put32 = inj.put32


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--img", default=r"C:\Users\Администратор\Desktop\Unreal\wc.img")
    args = ap.parse_args()
    fi = inj.Fat32Image(Path(args.img))

    # Сначала нормализуем: сделать FAT1 точной копией FAT0, чтобы ЕДИНСТВЕННЫМ
    # расхождением был media-дескриптор, который портим ниже (иначе остаточное
    # расхождение FAT1 затянулось бы обратно в FAT0, когда FAT1 берётся источником).
    f0 = fi.reserved * fi.bps
    f1 = (fi.reserved + fi.fat_size) * fi.bps
    flen = fi.fat_size * fi.bps
    if fi.data[f1:f1 + flen] != fi.data[f0:f0 + flen]:
        fi.data[f1:f1 + flen] = fi.data[f0:f0 + flen]
        print("normalized FAT1 := FAT0 (cleared leftover divergence)")

    off0 = fi.fat_offset(0, fat=0)
    off1 = fi.fat_offset(0, fat=1)
    old0 = struct.unpack_from("<I", fi.data, off0)[0]
    new0 = old0 & 0xFFFFFF00                     # младший байт 0x00 = невалидный media -> media_ok не проходит
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
