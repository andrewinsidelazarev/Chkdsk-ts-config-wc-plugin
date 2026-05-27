#!/usr/bin/env python3
"""Verify ChkDsk's orphaned-LFN detect (walk_mode 5) and repair (walk_mode 6) in the
deterministic Z80 simulator, against a corrupted copy of test.img.

Asserts:
  * scan reports  v_orphlfn == injected orphan-LFN entry count;
  * a sector-straddling orphan run was part of the corruption (cross-sector path);
  * repair zeroes (0xE5) EXACTLY the orphan LFN entries — the only bytes that change
    between the pre- and post-repair image are those first-bytes;
  * the VALID long-named file (LFN + live short) is left completely intact;
  * the FAT is not touched.
"""
import importlib.util as iu
import shutil
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sim_chkdsk as S                                   # Sim, parse_sym
import make_orphan_lfn as O                              # inject()

inj = O.inj                                              # Fat32Image module


def load_fat_region(data: bytes):
    resv = struct.unpack_from("<H", data, 14)[0]
    nfat = data[16]
    fatsz = struct.unpack_from("<I", data, 36)[0]
    start = resv * 512
    return start, start + nfat * fatsz * 512


def main():
    sym = S.parse_sym(HERE.parent / "src" / "dbg.sym")
    wmf = HERE.parent / "src" / "CHKDSK.WMF"
    src_img = Path(__import__("os").environ.get("CHKDSK_IMG", str(HERE / "test.img")))
    copy = HERE / "test_orphan.img"
    ok = True
    try:
        shutil.copy(src_img, copy)

        # --- corrupt: inject orphaned LFN runs into the root directory ---
        fi = inj.Fat32Image(copy)
        meta = O.inject(fi, fi.root_cluster)
        fi.save()
        expected = meta["expected"]
        snapshot = bytearray(copy.read_bytes())          # pre-repair image
        print(f"injected: orphan LFN entries={expected}  straddle_slot={meta['straddle_slot']}"
              f"  straddled={meta['straddled']}")
        if not meta["straddled"]:
            ok = False; print("  FAIL: expected a sector-straddling run for cross-sector coverage")

        # --- detection: run full scan, read v_orphlfn ---
        sim = S.Sim(wmf, img=copy, writable=True)
        sim.reg.PC = sym["PLUGIN"]; sim.reg.SP = 0xBF00
        sim.run_until_pc(sym["PLUGIN.wait"], max_steps=20_000_000)
        got = sim.u32(sym["v_orphlfn"])
        print(f"detect: v_orphlfn={got} (expect {expected})")
        if got != expected:
            ok = False; print("  FAIL: orphan-LFN detection count mismatch")
        else:
            print("  OK: detection count matches")

        # --- repair: zero the orphan LFN entries ---
        sim.call(sym["repair_orphan_lfn"], max_steps=20_000_000)
        fixed = sim.u32(sym["v_orphfix"])
        sim.sdf.flush()
        post = bytearray(copy.read_bytes())
        print(f"repair: v_orphfix={fixed} (expect {expected})")
        if fixed != expected:
            ok = False; print("  FAIL: repaired-count mismatch")

        # every orphan LFN entry must now be 0xE5
        all_zeroed = all(post[off] == 0xE5 for off in meta["orph_offsets"])
        print(f"  base={meta['base']} orph_slots={meta['orph_slots']} straddle={meta['straddle_slot']}")
        missed = [(s, off, hex(post[off])) for s, off in zip(meta["orph_slots"], meta["orph_offsets"])
                  if post[off] != 0xE5]
        print(f"  all {len(meta['orph_offsets'])} orphan LFN entries marked 0xE5: {all_zeroed}  missed={missed}")
        if not all_zeroed:
            ok = False

        # the ONLY changed bytes must be exactly those orphan LFN first-bytes
        changed = [i for i in range(len(snapshot)) if snapshot[i] != post[i]]
        expected_changed = sorted(meta["orph_offsets"])
        if changed != expected_changed:
            ok = False
            extra = [c for c in changed if c not in set(expected_changed)]
            print(f"  FAIL: unexpected byte changes: {len(extra)} bytes outside orphan entries"
                  f" (e.g. {extra[:8]})")
        else:
            print(f"  OK: exactly {len(changed)} bytes changed, all orphan-LFN first bytes")

        # FAT region untouched
        fs, fe = load_fat_region(snapshot)
        fat_unchanged = snapshot[fs:fe] == post[fs:fe]
        print(f"  FAT unchanged: {fat_unchanged}")
        if not fat_unchanged:
            ok = False

        # valid long-named file still present and intact (re-parse)
        fi2 = inj.Fat32Image(copy)
        names = [e["name"] for e in fi2.parse_dir(fi2.root_cluster)]
        valid_name = O._name(2, 1)
        valid_ok = valid_name in names
        print(f"  valid long file {valid_name!r} still listed: {valid_ok}")
        if not valid_ok:
            ok = False

        # re-scan: no orphan LFN entries should remain
        sim2 = S.Sim(wmf, img=copy)
        sim2.reg.PC = sym["PLUGIN"]; sim2.reg.SP = 0xBF00
        sim2.run_until_pc(sym["PLUGIN.wait"], max_steps=20_000_000)
        residual = sim2.u32(sym["v_orphlfn"])
        print(f"  rescan after repair: v_orphlfn={residual} (expect 0)")
        if residual != 0:
            ok = False

        sim.sdf.close()
    except Exception as e:
        ok = False
        import traceback; traceback.print_exc()
    finally:
        try:
            copy.unlink()
        except OSError:
            pass

    print("\nRESULT:", "ALL OK" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
