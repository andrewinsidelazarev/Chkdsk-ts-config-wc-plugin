#!/usr/bin/env python3
"""Внести искусственные ПОТЕРЯННЫЕ (lost) кластеры в FAT32 wc.img для проверки
ремонта ChkDsk.

«Потерянный кластер» = запись FAT помечена занятой (ненулевая), но НИ ОДНА запись
каталога на неё не ссылается -> ChkDsk обязан её освободить.

Метит цепочки в ОБОИХ окнах ChkDsk, чтобы задействовать оконный ремонт:
  - одну цепочку в ВЕРХНЕЙ части окна 0 (кластер > 65536) -> проверяет 17-битный
    путь bm_addr (кластеры 65536..131071 окна на 131072 кластера),
  - одну цепочку в окне 1 (кластер >= 131072), если диск достаточно большой,
  - одну одиночную (EOC) цепочку внизу окна 0.

Все копии FAT обновляются вместе, поэтому рассинхрон зеркала НЕ вносится (только
потерянные кластеры). Обратимо: ENTER=repair в ChkDsk освободит их обратно.

Запуск: python make_lost_clusters.py [--img PATH]
"""
import argparse
import importlib.util as iu
from pathlib import Path

HERE = Path(__file__).resolve().parent
_sp = iu.spec_from_file_location("inj", HERE.parent / "inject_chkdsk_to_wc_img.py")
inj = iu.module_from_spec(_sp)
_sp.loader.exec_module(inj)
EOC = inj.EOC
WINDOW = 131072                      # WINDOW_CLUSTERS в ChkDsk (должно совпадать со сборкой)


def last_cluster_plus1(fi):
    return (fi.total_sectors - fi.first_data_sector) // fi.spc + 2


def grab_free(fi, lo, hi, need):
    out = []
    for c in range(max(2, lo), hi):
        if fi.get_fat(c) == 0:
            out.append(c)
            if len(out) == need:
                break
    return out


def make_lost_chain(fi, clusters):
    for i, c in enumerate(clusters):
        fi.set_fat(c, clusters[i + 1] if i + 1 < len(clusters) else EOC)


def count_free(fi, top):
    return sum(1 for c in range(2, top) if fi.get_fat(c) == 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--img", default=r"C:\Users\Администратор\Desktop\Unreal\wc.img")
    args = ap.parse_args()

    fi = inj.Fat32Image(Path(args.img))
    top = last_cluster_plus1(fi)
    free_before = count_free(fi, top)

    chains = []
    w0hi = grab_free(fi, 65536, min(WINDOW, top), 7)
    if len(w0hi) == 7:
        chains.append(("window0 high (>65536, tests 17-bit bm_addr)", w0hi))
    if top > WINDOW:
        w1 = grab_free(fi, WINDOW, top, 9)
        if len(w1) == 9:
            chains.append(("window1 (>=131072)", w1))
    single = grab_free(fi, 2, 65536, 1)
    if single:
        chains.append(("window0 low (single EOC cluster)", single))

    if not chains:
        print("no free clusters found - nothing changed")
        return 1

    total = 0
    for name, cl in chains:
        make_lost_chain(fi, cl)
        total += len(cl)
        print(f"  {name}: {len(cl)} clusters, {cl[0]}..{cl[-1]}")
    fi.save()

    free_after = count_free(fi, top)
    print(f"\ntotal lost clusters introduced: {total}")
    print(f"free clusters: {free_before} -> {free_after} (-{free_before - free_after})")
    print("all FAT copies updated -> no backup-FAT mismatch, only lost clusters")
    print(f"ChkDsk should report  lost allocation units = {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
