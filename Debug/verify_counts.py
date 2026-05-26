#!/usr/bin/env python3
"""Independently count files/dirs in wc.img (same rules as the plugin)
and compare against the plugin's reported numbers.

Guarded against FAT cycles / cross-links (which would otherwise hang).
"""
import argparse
import importlib.util
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("inj", here.parent / "inject_chkdsk_to_wc_img.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

ATTR_DIR = 0x10
ATTR_VOL = 0x08
ATTR_LFN = 0x0F


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--img", default=r"C:\Users\Администратор\Desktop\Unreal\wc.img")
    ap.add_argument("--depth-max", type=int, default=16)
    args = ap.parse_args()

    img = m.Fat32Image(Path(args.img))

    # ---- guarded cluster_chain: stop on revisit or absurd length ----
    orig_max = (img.total_sectors // img.spc) + 4
    def guarded_chain(start):
        chain, cur, seen = [], start, set()
        while 2 <= cur < 0x0FFFFFF8:
            if cur in seen:
                print(f"  !! FAT CYCLE detected at cluster {cur}", flush=True)
                break
            seen.add(cur)
            chain.append(cur)
            if len(chain) > orig_max:
                print(f"  !! chain too long from {start}", flush=True)
                break
            nxt = img.get_fat(cur)
            if nxt >= 0x0FFFFFF8:
                break
            cur = nxt
        return chain
    img.cluster_chain = guarded_chain

    files = dirs = 0
    visited_dirs = set()

    def walk(cluster, depth, path):
        nonlocal files, dirs
        if depth >= args.depth_max:
            print(f"  (depth cap at {path})", flush=True)
            return
        if cluster in visited_dirs:
            print(f"  !! DIR CYCLE: {path} -> cluster {cluster} already visited", flush=True)
            return
        visited_dirs.add(cluster)
        for e in img.parse_dir(cluster):
            attr = e["attr"]
            if attr == ATTR_LFN or (attr & ATTR_VOL):
                continue
            name = img.short_to_name(e["short"])
            if attr & ATTR_DIR:
                if name in (".", ".."):     # check BEFORE any stripping
                    continue
                dirs += 1
                walk(e["cluster"], depth + 1, path + name + "/")
            else:
                files += 1

    print(f"image: {args.img}  root_cluster={img.root_cluster}", flush=True)
    walk(img.root_cluster, 0, "/")
    print(f"files = {files}  (#{files:04X})", flush=True)
    print(f"dirs  = {dirs}  (#{dirs:04X})", flush=True)


if __name__ == "__main__":
    main()
