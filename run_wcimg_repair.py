#!/usr/bin/env python3
"""Full Z80 verification of repair_lost on a COPY of the real wc.img.
Definitive safety check before any on-disk repair: confirms the actual
Z80 implementation frees exactly the orphaned clusters and leaves every
reachable cluster (file/dir chains) untouched, on real 201566-cluster data.
The real wc.img is never written (we operate on wc_repair_test.img).
Slow (~30-40 min) - run in background.
"""
import importlib.util, struct, shutil, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
WC = Path(r"C:\Users\Администратор\Desktop\Unreal\wc.img")
copy = HERE / "wc_repair_test.img"

sp = importlib.util.spec_from_file_location("s", HERE / "sim_chkdsk.py")
m = importlib.util.module_from_spec(sp); sp.loader.exec_module(m)
sym = m.parse_sym(HERE / "src" / "dbg.sym")
wmf = HERE / "src" / "CHKDSK.WMF"


def log(s):
    print(f"[{time.strftime('%H:%M:%S')}] {s}", flush=True)


def read_fats(p):
    with open(p, "rb") as f:
        d = f.read(512)
        resv = struct.unpack_from("<H", d, 14)[0]
        fatsz = struct.unpack_from("<I", d, 36)[0]
        f.seek(resv * 512)
        fat0 = f.read(fatsz * 512)
        fat1 = f.read(fatsz * 512)
    return fat0, fat1


def main():
    log(f"WMF={wmf.stat().st_size}B  copying wc.img ({WC.stat().st_size}B) -> {copy.name}")
    shutil.copy(WC, copy)

    log("ground truth: reachable set + FAT(before) ...")
    reach0 = m.reachable_set(copy)
    free0, _ = m.expected_free(copy)
    ef0, efb0, ed0, edb0 = m.expected_walk(copy)
    fatA, _ = read_fats(copy)
    log(f"  reachable={len(reach0)}  free={free0}  files={ef0} dirs={ed0} fb={efb0}")

    log("Z80: report scan (run to .wait) ... [~8 min]")
    t = time.time()
    sim = m.Sim(wmf, img=copy, writable=True)
    sim.reg.PC = sym["PLUGIN"]; sim.reg.SP = 0xBF00
    steps = sim.run_until_pc(sym["PLUGIN.wait"], max_steps=400_000_000)
    lost_before = sim.u32(sym["v_lost"])
    log(f"  report done: {steps} steps, {time.time()-t:.0f}s  v_lost={lost_before} v_free={sim.u32(sym['v_free'])}")

    log("Z80: repair_lost (4 windows: mark reachable + free orphans, write FAT0+FAT1) ... [~25 min]")
    t = time.time()
    sim.call(sym["repair_lost"], max_steps=4_000_000_000)
    recovered = sim.u32(sym["v_recovered"])
    sim.sdf.flush()
    log(f"  repair done: {time.time()-t:.0f}s  recovered={recovered}")

    log("verifying written image ...")
    fatB, fat1 = read_fats(copy)
    ef1, efb1, ed1, edb1 = m.expected_walk(copy)
    free1, _ = m.expected_free(copy)
    reach_unchanged = all(fatA[c*4:c*4+4] == fatB[c*4:c*4+4] for c in reach0)
    changed = [c for c in range(2, len(fatA)//4) if fatA[c*4:c*4+4] != fatB[c*4:c*4+4]]
    only_orphans = all(
        c not in reach0
        and struct.unpack_from("<I", fatA, c*4)[0] != 0
        and struct.unpack_from("<I", fatB, c*4)[0] == 0
        for c in changed)

    log(f"  tree before f={ef0} d={ed0} fb={efb0} | after f={ef1} d={ed1} fb={efb1}")
    log(f"  free {free0}->{free1} (+{free1-free0})  changed={len(changed)}  FAT0==FAT1:{fatB==fat1}")
    checks = {
        "recovered==report-lost": recovered == lost_before,
        "tree intact": (ef1, efb1, ed1, edb1) == (ef0, efb0, ed0, edb0),
        "free grew by recovered": free1 == free0 + recovered,
        "FAT0==FAT1": fatB == fat1,
        "reachable FAT unchanged (NO data loss)": reach_unchanged,
        "only orphans zeroed & count matches": only_orphans and len(changed) == recovered,
    }
    allok = all(checks.values())
    for k, v in checks.items():
        log(f"    [{'OK' if v else 'FAIL'}] {k}")
    try:
        sim.sdf.close()
    except Exception:
        pass
    try:
        copy.unlink()
    except OSError:
        log("  (note: copy left on disk, unlink failed)")
    log("RESULT: " + ("ALL OK - repair verified SAFE on real wc.img data" if allok else "FAILURES - DO NOT use repair"))


if __name__ == "__main__":
    main()
