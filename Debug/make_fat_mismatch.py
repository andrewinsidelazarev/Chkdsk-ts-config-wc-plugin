#!/usr/bin/env python3
"""Испортить несколько секторов FAT1 (резервной копии FAT) — для проверки
зеркального ремонта FAT в ChkDsk.

Пишет неверные записи ТОЛЬКО в FAT1 (FAT0 не трогает) → N расходящихся секторов FAT
→ ChkDsk сообщает N «backup-FAT mismatches», а ENTER=repair копирует FAT0 поверх
FAT1 (media-дескриптор FAT0[0] остаётся валидным → FAT0 выбирается как хороший
источник).

ChkDsk считает расхождения ПОСЕКТОРНО (cmp_fat_mirror), поэтому каждый испорченный
сектор добавляет ровно 1. Трогаем по одной записи в каждом из N разных секторов FAT.

Запуск: python make_fat_mismatch.py [--img PATH] [--sectors N]
"""
import argparse
import importlib.util as iu
from pathlib import Path

HERE = Path(__file__).resolve().parent
_sp = iu.spec_from_file_location("inj", HERE.parent / "inject_chkdsk_to_wc_img.py")
inj = iu.module_from_spec(_sp)
_sp.loader.exec_module(inj)
put32 = inj.put32

ENTRIES_PER_SECTOR = 128                  # 512 байт / 4 байта на запись FAT32


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--img", default=r"C:\Users\Администратор\Desktop\Unreal\wc.img")
    ap.add_argument("--sectors", type=int, default=5, help="number of FAT1 sectors to corrupt")
    args = ap.parse_args()

    fi = inj.Fat32Image(Path(args.img))
    done = []
    for k in range(1, args.sectors + 1):
        c = ENTRIES_PER_SECTOR * k + 7    # кластер в отдельном секторе FAT (минуем сектор 0 = media desc)
        fat0 = fi.get_fat(c)              # значение FAT0 (не трогаем)
        bad = 0x0FFFFFF7 if (fat0 & 0x0FFFFFFF) != 0x0FFFFFF7 else 0x00000001
        put32(fi.data, fi.fat_offset(c, fat=1), bad)   # пишем ТОЛЬКО запись FAT1 -> расходится с FAT0
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
