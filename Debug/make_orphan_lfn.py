#!/usr/bin/env python3
"""Внести ОСИРОТЕВШИЕ LFN-записи в FAT32-образ — воспроизводит баг удаления Wild
Commander, чтобы прогнать детект/ремонт ChkDsk.

`DELEN` в CORE32 у WC помечает удалённой (0xE5) ТОЛЬКО короткую 8.3-запись, оставляя
предшествующие записи длинного имени (LFN, атрибут 0x0F) нетронутыми. Каждый файл/
каталог с длинным именем (все кириллические / смешанного регистра) оставляет прогон
осиротевших записей 0x0F, указывающих на уже удалённую короткую запись — порча
структуры каталога FAT32.

Скрипт воссоздаёт это состояние в каталоге:
  * один ВАЛИДНЫЙ файл с длинным именем (прогон LFN + ЖИВАЯ короткая запись) — ChkDsk
    обязан его ПРОПУСТИТЬ (чексум совпадает с владельцем), доказывая, что ремонт не
    удаляет хорошие длинные имена;
  * несколько ОСИРОТЕВШИХ файлов с длинными именами (прогон LFN + короткая, помеченная
    0xE5), в т.ч. один, чей прогон LFN ПЕРЕСЕКАЕТ границу 512-байтного сектора
    (проверяет добивку межсекторного хвоста). Файлы нулевой длины (первый кластер 0),
    поэтому кластеры НЕ затрагиваются -> нет lost-кластеров / правок FAT, изолируем
    именно случай осиротевших LFN.

Вся рабочая область предзаполняется 0xE5 (удалёнными) заглушками, чтобы прогоны
можно было размещать в выбранных слотах без 0x00 (конец каталога), обрывающего скан.

Запуск (для образа на железе):  python make_orphan_lfn.py [--img PATH] [--dir NAME]
Импортируемо: inject(fi, dir_cluster) -> dict(metadata) для тест-харнесса.
"""
import argparse
import importlib.util as iu
from pathlib import Path

HERE = Path(__file__).resolve().parent
_sp = iu.spec_from_file_location("inj", HERE.parent / "inject_chkdsk_to_wc_img.py")
inj = iu.module_from_spec(_sp)
_sp.loader.exec_module(inj)

LFN_POS = [1, 3, 5, 7, 9, 14, 16, 18, 20, 22, 24, 28, 30]   # слоты символов в записи 0x0F


def build_lfn_entries(long_name: str, checksum: int):
    """LFN-записи для long_name, в порядке на диске (старшая последовательность первой)."""
    units = long_name.encode("utf-16-le")
    chars = [units[i:i + 2] for i in range(0, len(units), 2)]
    n = (len(chars) + 12) // 13                      # ceil(len/13) записей LFN
    out = []
    for seq in range(1, n + 1):
        e = bytearray(32)
        e[11] = 0x0F                                 # атрибут LFN
        e[12] = 0x00
        e[13] = checksum                             # == чексум короткого имени (во всех записях)
        base = (seq - 1) * 13
        for k, pos in enumerate(LFN_POS):
            idx = base + k
            if idx < len(chars):
                e[pos:pos + 2] = chars[idx]
            elif idx == len(chars):
                e[pos:pos + 2] = b"\x00\x00"         # терминатор имени
            else:
                e[pos:pos + 2] = b"\xff\xff"         # заполнитель
        e[0] = seq | (0x40 if seq == n else 0)       # 0x40 помечает последнюю логическую часть
        out.append(bytes(e))
    out.reverse()                                    # на диске: seq N (0x40) первой .. seq 1 последней
    return out


def _name(n_lfn: int, i: int) -> str:
    """Имя, требующее ровно n_lfn записей LFN (длина в ((n-1)*13, n*13])."""
    length = (n_lfn - 1) * 13 + 5
    return (f"orphan-file-{i:02d}-" + "x" * (n_lfn * 13)) [:length]


def inject(fi, dir_cluster):
    """Разместить валидный + несколько осиротевших файлов с длинными именами в
    dir_cluster. Возвращает metadata: ожидаемое число осиротевших записей LFN, байтовые
    смещения каждой осиротевшей записи LFN (должны стать 0xE5 после ремонта) и признак,
    был ли создан straddle (прогон через границу сектора).
    """
    cs = fi.cluster_size
    chain = fi.cluster_chain(dir_cluster)

    def slot_off(slot):                               # абсолютное байтовое смещение 32-байтного слота
        rel = slot * 32
        return fi.cluster_offset(chain[rel // cs]) + rel % cs

    REGION = 48
    base = fi.find_free_dir_slots(dir_cluster, REGION + 1)   # +1 оставляет 0x00-терминатор после
    chain = fi.cluster_chain(dir_cluster)                    # мог вырасти (авто-расширение)
    for s in range(base, base + REGION):                     # предзаполнить область удалёнными заглушками
        fi.data[slot_off(s)] = 0xE5

    # раскладка: [valid 2-LFN][orphan 1-LFN][orphan 3-LFN][orphan 2-LFN STRADDLE][orphan 2-LFN]
    runs = []
    cur = base
    runs.append(("valid", 2, False)); cur += 3
    runs.append(("orph",  1, True));  cur += 2
    runs.append(("orph",  3, True));  cur += 4
    t = cur                                                   # straddle: первый слот с idx%16==15
    while t % 16 != 15:
        t += 1
    straddle_at = t
    runs2 = [("valid", 2, False, base),
             ("orph", 1, True,  base + 3),
             ("orph", 3, True,  base + 5),
             ("orph", 2, True,  straddle_at),                 # прогон LFN пересекает границу сектора
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
        # записать LFN-записи, затем короткую запись, подряд
        for j, le in enumerate(lfns):
            fi.data[slot_off(slot + j):slot_off(slot + j) + 32] = le
        fi.data[slot_off(slot + n_lfn):slot_off(slot + n_lfn) + 32] = short_ent
        if deleted:
            fi.data[slot_off(slot + n_lfn)] = 0xE5            # баг WC: удалить ТОЛЬКО короткую запись
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
