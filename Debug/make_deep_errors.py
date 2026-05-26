#!/usr/bin/env python3
"""Plant deep-check (SPACE) errors into a FAT32 image: a cross-link and a
bad-size file. ChkDsk's deep check is DETECT-only (it reports Cross-linked /
Bad-size, it does not repair them), so this writes a sidecar backup and can
revert with --restore.

  cross-link: splice file B's last cluster (EOC) to point at file A's first
              cluster -> A's clusters are claimed by both chains.
  bad-size  : grow a file's directory size field by one cluster so the chain
              length no longer matches ceil(size / clusterSize).

Usage:
    python make_deep_errors.py [--img PATH]            # plant errors
    python make_deep_errors.py [--img PATH] --restore  # revert from sidecar
"""
import argparse
import json
import importlib.util as iu
from pathlib import Path

HERE = Path(__file__).resolve().parent
_sp = iu.spec_from_file_location("inj", HERE.parent / "inject_chkdsk_to_wc_img.py")
inj = iu.module_from_spec(_sp)
_sp.loader.exec_module(inj)
EOC = inj.EOC


def find_files(fi):
    out, q, seen = [], [fi.root_cluster], set()
    while q:
        dc = q.pop(0)
        if dc in seen:
            continue
        seen.add(dc)
        for e in fi.parse_dir(dc):
            a = e["attr"]
            if a == 0x0F or (a & 0x08):
                continue
            if a & 0x10:
                if fi.short_to_name(e["short"]) not in (".", ".."):
                    q.append(e["cluster"])
            elif e["cluster"] >= 2:
                out.append({"dir": dc, "index": e["index"], "name": e["name"],
                            "start": e["cluster"], "size": e["size"],
                            "chain": fi.cluster_chain(e["cluster"])})
    return out


def set_dir_size(fi, dir_cluster, index, size):
    for k in range(4):
        fi.write_dir_byte(dir_cluster, index * 32 + 28 + k, (size >> (8 * k)) & 0xFF)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--img", default=r"C:\Users\Администратор\Desktop\Unreal\wc.img")
    ap.add_argument("--restore", action="store_true")
    args = ap.parse_args()
    img = Path(args.img)
    side = img.with_suffix(img.suffix + ".deepbak")
    fi = inj.Fat32Image(img)

    if args.restore:
        if not side.exists():
            print("no sidecar backup found - nothing to restore")
            return 1
        bak = json.loads(side.read_text())
        set_dir_size(fi, bak["bs_dir"], bak["bs_index"], bak["bs_orig_size"])
        set_dir_size(fi, bak["b_dir"], bak["b_index"], bak["b_orig_size"])   # B's size (repair_size may have changed it)
        fi.set_fat(bak["xl_cluster"], bak["xl_orig"])
        fi.save()
        side.unlink()
        print(f"restored: bad-size size -> {bak['bs_orig_size']}, B size -> {bak['b_orig_size']}, FAT[{bak['xl_cluster']}] -> {bak['xl_orig']:#x}")
        return 0

    files = [f for f in find_files(fi) if f["chain"]]
    if len(files) < 2:
        print("need >=2 files with clusters to plant errors")
        return 1
    files.sort(key=lambda f: len(f["chain"]))
    a = files[0]                       # smallest chain -> few cross-linked clusters
    b = next(f for f in files if f["start"] != a["start"])
    bsf = files[-1] if files[-1]["start"] not in (a["start"], b["start"]) else b

    bak = {"bs_dir": bsf["dir"], "bs_index": bsf["index"], "bs_orig_size": bsf["size"],
           "b_dir": b["dir"], "b_index": b["index"], "b_orig_size": b["size"],
           "xl_cluster": b["chain"][-1], "xl_orig": fi.get_fat(b["chain"][-1])}
    side.write_text(json.dumps(bak))

    # bad-size: grow the size field by one cluster (chain unchanged)
    new_size = bsf["size"] + fi.cluster_size
    set_dir_size(fi, bsf["dir"], bsf["index"], new_size)
    # cross-link: B's last cluster (was EOC) now points into A's chain
    fi.set_fat(b["chain"][-1], a["start"])
    fi.save()

    print(f"bad-size : '{bsf['name']}' size {bsf['size']} -> {new_size} (chain {len(bsf['chain'])} clusters) -> Bad-size++")
    print(f"cross-link: '{b['name']}'.last cluster {b['chain'][-1]} -> '{a['name']}'.start {a['start']} "
          f"(A chain={len(a['chain'])} clusters shared) -> Cross-linked++")
    print(f"\nSPACE=deep check should now report Cross-linked > 0 and Bad-size > 0")
    print("restore with:  python make_deep_errors.py --restore")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
