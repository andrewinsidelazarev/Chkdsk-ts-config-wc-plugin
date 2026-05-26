#!/usr/bin/env python3
"""Deterministic Z80 harness for the ChkDsk plugin routines.

Uses the bundled pure-Python cburbridge Z80 core (copied locally into
_z80_lib_cburbridge). Loads the assembled CHKDSK.WMF code at #8000 and
can call/run routines with an instruction limit, so hangs (e.g. the
add32 B-clobber infinite loop) surface as a TimeoutError instead of a
round-trip through Unreal.

Pure-logic tests only (fmt_dec32, geometry compute). SD I/O is mocked
minimally; the BPB is injected directly for the geometry test.
"""
from __future__ import annotations
import contextlib
import io
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "_z80_lib_cburbridge" / "src"))
from z80 import instructions, registers, util  # noqa: E402

RETURN_MARKER = 0xFFFE


def parse_sym(path: Path) -> dict:
    sym = {}
    for line in path.read_text(encoding="latin-1").splitlines():
        if ": EQU " in line:
            name, val = line.split(": EQU ")
            sym[name.strip()] = int(val.strip(), 16) & 0xFFFF
    return sym


import os
# Fast by default: small synthetic test.img (~2s harness vs ~8min on wc.img).
# Set CHKDSK_IMG=...\wc.img for the final real-disk verification run.
IMG = Path(os.environ.get("CHKDSK_IMG", str(Path(__file__).resolve().parent / "test.img")))


def expected_free(img: Path):
    """Live FAT free-cluster ground truth (changes whenever the plugin
    file is re-injected, since that re-allocates its clusters)."""
    with open(img, "rb") as f:
        d = f.read(512)
        spc, resv, nfat = d[13], struct.unpack_from("<H", d, 14)[0], d[16]
        fatsz, tot = struct.unpack_from("<I", d, 36)[0], struct.unpack_from("<I", d, 32)[0]
        total = (tot - (resv + nfat * fatsz)) // spc
        f.seek(resv * 512)
        fat = f.read(fatsz * 512)
    free = sum(1 for c in range(2, total + 2)
               if (struct.unpack_from("<I", fat, c * 4)[0] & 0x0FFFFFFF) == 0)
    return free, free * spc * 512


def expected_lost(img: Path):
    """Lost allocation units (allocated - size-based-used), matching the
    plugin's cheap method (exact when files are well-formed)."""
    import importlib.util as iu
    sp = iu.spec_from_file_location("inj", HERE.parent / "inject_chkdsk_to_wc_img.py")
    inj = iu.module_from_spec(sp); sp.loader.exec_module(inj)
    fi = inj.Fat32Image(img)
    cs = fi.spc * 512
    with open(img, "rb") as f:
        d = f.read(512)
        resv = struct.unpack_from("<H", d, 14)[0]; nfat = d[16]
        fatsz = struct.unpack_from("<I", d, 36)[0]; tot = struct.unpack_from("<I", d, 32)[0]
    total = (tot - (resv + nfat * fatsz)) // fi.spc
    free = sum(1 for c in range(2, total + 2) if fi.get_fat(c) == 0)
    allocated = total - free
    exp = dirclus = 0
    q, seen = [fi.root_cluster], set()
    while q:
        dc = q.pop(0)
        if dc in seen:
            continue
        seen.add(dc); dirclus += len(fi.cluster_chain(dc))
        for e in fi.parse_dir(dc):
            a = e["attr"]
            if a == 0x0F or (a & 0x08):
                continue
            if a & 0x10:
                if fi.short_to_name(e["short"]) in (".", ".."):
                    continue
                q.append(e["cluster"])
            else:
                exp += (e["size"] + cs - 1) // cs
    used = exp + dirclus
    return max(0, allocated - used)


def reachable_set(img: Path):
    """Set of all clusters reachable from root via dir+file chains."""
    import importlib.util as iu
    sp = iu.spec_from_file_location("inj", HERE.parent / "inject_chkdsk_to_wc_img.py")
    inj = iu.module_from_spec(sp); sp.loader.exec_module(inj)
    fi = inj.Fat32Image(img)
    reach = set()
    q, seen = [fi.root_cluster], set()
    while q:
        dc = q.pop(0)
        if dc in seen:
            continue
        seen.add(dc)
        reach.update(fi.cluster_chain(dc))
        for e in fi.parse_dir(dc):
            a = e["attr"]
            if a == 0x0F or (a & 0x08):
                continue
            if a & 0x10:
                if fi.short_to_name(e["short"]) in (".", ".."):
                    continue
                q.append(e["cluster"])
            elif e["cluster"] >= 2:
                reach.update(fi.cluster_chain(e["cluster"]))
    return reach


def expected_walk(img: Path):
    """Live dir-tree ground truth: (files, filebytes, dirs, dirbytes)."""
    import importlib.util as iu
    sp = iu.spec_from_file_location("inj", HERE.parent / "inject_chkdsk_to_wc_img.py")
    inj = iu.module_from_spec(sp); sp.loader.exec_module(inj)
    fi = inj.Fat32Image(img)
    files = filebytes = dirs = dirclus = 0
    q, seen = [fi.root_cluster], set()
    while q:
        dc = q.pop(0)
        if dc in seen:
            continue
        seen.add(dc)
        dirclus += len(fi.cluster_chain(dc))
        for e in fi.parse_dir(dc):
            a = e["attr"]
            if a == 0x0F or (a & 0x08):
                continue
            if a & 0x10:
                if fi.short_to_name(e["short"]) in (".", ".."):
                    continue
                dirs += 1; q.append(e["cluster"])
            else:
                files += 1; filebytes += e["size"]
    return files, filebytes, dirs, dirclus * fi.spc * 512


class Sim:
    def __init__(self, wmf: Path, img: Path = None, writable: bool = False):
        self.mem = bytearray(0x10000)
        self.reg = registers.Registers()
        with contextlib.redirect_stdout(io.StringIO()):
            self.ins = instructions.InstructionSet(self.reg)
        code = wmf.read_bytes()[512:]            # skip 512-byte header
        self.mem[0x8000:0x8000 + len(code)] = code
        self.mem[0x6006] = 0xC9                  # _WCAPI -> RET (mock WC API as no-op)
        self.reg.SP = 0xBF00
        # SD (Z-Controller) mock backed by wc.img
        self.sdf = open(img, "r+b" if writable else "rb") if img else None
        self.cs = False
        self.cmd = []
        self.rq = []
        self.wmode = None          # None | 'token' | 'data'
        self.wbuf = []
        self.wsector = 0

    # --- SD mock: ports #77 (CS) / #57 (data) ---
    def in_port(self, port):
        if (port & 0xFF) == 0x57:
            return self.rq.pop(0) if self.rq else 0xFF
        return 0xFF

    def out_port(self, port, val):
        low = port & 0xFF
        if low == 0x77:                          # chip select
            self.cs = (val & 0x03) == 0x01
            self.cmd = []
            self.wmode = None
            return
        if low != 0x57 or not self.cs:
            return
        if self.wmode == "token":                # waiting for data-start token
            if val in (0xFE, 0xFC):
                self.wmode = "data"; self.wbuf = []
            return                               # ignore #FF / stop token #FD
        if self.wmode == "data":                 # collecting 512 data bytes
            self.wbuf.append(val)
            if len(self.wbuf) == 512:
                self.sdf.seek(self.wsector * 512)
                self.sdf.write(bytes(self.wbuf))
                self.wsector += 1
                self.wmode = "token"             # ready for next block (multi-write)
                self.rq = [0x05, 0xFF]           # data accepted, busy-done
            return
        if val == 0xFF and not self.cmd:
            return                               # leading dummy clock
        self.cmd.append(val)
        if len(self.cmd) == 6:
            self._sd_command(self.cmd)
            self.cmd = []

    def _sd_command(self, c):
        cmd = c[0]
        addr = (c[1] << 24) | (c[2] << 16) | (c[3] << 8) | c[4]
        if cmd == 0x52:                          # CMD18 multi-block read
            sector = addr // 512                 # byte addressing (SDBSF=0)
            self.sdf.seek(sector * 512)
            data = self.sdf.read(512)
            self.rq = [0x00, 0xFE] + list(data)  # R1, data token, 512 bytes
        elif cmd == 0x59:                        # CMD25 multi-block write
            self.wsector = addr // 512
            self.wmode = "token"
            self.rq = [0x00]                     # R1 to CMD25
        elif cmd == 0x4C:                        # CMD12 stop
            self.rq = [0x00]

    # memory helpers
    def rd(self, a, n): return bytes(self.mem[a:a + n])
    def wr(self, a, data): self.mem[a:a + len(data)] = data
    def w16(self, a, v): self.mem[a] = v & 0xFF; self.mem[a + 1] = (v >> 8) & 0xFF
    def u32(self, a): return struct.unpack_from("<I", self.mem, a)[0]

    # ref / ports
    def _read_ref(self, ref):
        if ref >= 0x10000:
            return self.in_port(ref & 0xFFFF)
        return self.mem[ref & 0xFFFF]

    def _write_ref(self, ref, value):
        if ref >= 0x10000:
            self.out_port(ref & 0xFFFF, value & 0xFF)
        else:
            self.mem[ref & 0xFFFF] = value & 0xFF

    def step(self):
        ins, args = False, ()
        while not ins:
            op = self.mem[self.reg.PC]
            ins, args = self.ins << op
            self.reg.PC = util.inc16(self.reg.PC)
        reads = ins.get_read_list(args)
        data = [self._read_ref(r) for r in reads]
        for ref, value in ins.execute(data, args):
            self._write_ref(ref, value)

    def run_until_pc(self, pc, max_steps=300000):
        steps = 0
        while self.reg.PC != pc:
            if steps >= max_steps:
                raise TimeoutError(f"HANG: PC=#{self.reg.PC:04X} target=#{pc:04X} after {steps} steps")
            self.step()
            steps += 1
        return steps

    def call(self, addr, a=0, b=0, c=0, d=0, e=0, h=0, l=0, max_steps=300000):
        self.reg.A, self.reg.B, self.reg.C = a & 0xFF, b & 0xFF, c & 0xFF
        self.reg.D, self.reg.E, self.reg.H, self.reg.L = d & 0xFF, e & 0xFF, h & 0xFF, l & 0xFF
        sp = (self.reg.SP - 2) & 0xFFFF
        self.w16(sp, RETURN_MARKER)
        self.reg.SP = sp
        self.reg.PC = addr & 0xFFFF
        return self.run_until_pc(RETURN_MARKER, max_steps)


def main():
    sym = parse_sym(HERE.parent / "src" / "dbg.sym")
    wmf = HERE.parent / "src" / "CHKDSK.WMF"
    ok = True

    # --- Test 1: fmt_dec32 (decimal formatting) ---
    print("=== fmt_dec32 ===")
    SRC, FLD, W = 0x9000, 0x9010, 11
    for v in (0, 10, 99, 512, 201566, 103201792, 4294967295):
        sim = Sim(wmf)
        sim.wr(SRC, struct.pack("<I", v))
        sim.call(sym["fmt_dec32"], h=SRC >> 8, l=SRC & 0xFF, d=FLD >> 8, e=FLD & 0xFF, b=W)
        out = sim.rd(FLD, W).decode("latin1")
        want = f"{v:>{W}}"
        status = "OK" if out == want else "FAIL"
        if out != want:
            ok = False
        print(f"  {v:>11} -> '{out}'  [{status}]")

    # (Test 2 geometry-from-bpb_ready skipped: redundant with Test 3 and the
    #  FAT-mirror check doubles scan time. Full flow below covers it.)

    # --- Test 3: full flow via SD mock (PC=#8000, reads wc.img) ---
    print("=== full flow via SD mock (PC=#8000 -> .show) ===")
    try:
        sim = Sim(wmf, img=IMG)
        sim.reg.PC = sym["PLUGIN"]
        sim.reg.SP = 0xBF00
        steps = sim.run_until_pc(sym["PLUGIN.wait"], max_steps=20_000_000)
        print(f"  reached .show in {steps} steps (SD read + FAT scan OK)")
        for label in ("txt_space", "txt_unit", "txt_units", "txt_avail", "txt_aunits"):
            if label in sym:
                print(f"  {label}: '{sim.rd(sym[label], 11).decode('latin1')}'")
        free = sim.u32(sym["v_free"])
        avail = sim.u32(sym["v_bytesavail"])
        exp_free, exp_avail = expected_free(IMG)   # computed live (injects change it)
        print(f"  v_free={free} (expect {exp_free})   v_bytesavail={avail} (expect {exp_avail})")
        if (free, avail) != (exp_free, exp_avail):
            ok = False
            print("  FAIL: FAT free-scan mismatch")
        else:
            print("  OK: FAT free-scan matches live ground truth")
        # dir-walk results
        fc = sim.u32(sym["v_filecnt"]); fb = sim.u32(sym["v_filebytes"])
        dc = sim.u32(sym["v_dircnt"]); db_ = sim.u32(sym["v_dirbytes"])
        ef, efb, ed, edb = expected_walk(IMG)
        print(f"  files={fc} (exp {ef})  filebytes={fb} (exp {efb})")
        print(f"  dirs={dc} (exp {ed})  dirbytes={db_} (exp {edb})")
        if (fc, fb, dc, db_) != (ef, efb, ed, edb):
            ok = False
            print("  FAIL: dir-walk mismatch")
        else:
            print("  OK: dir-walk matches live ground truth")
        # lost clusters
        lost = sim.u32(sym["v_lost"]); elost = expected_lost(IMG)
        print(f"  lost={lost} (exp {elost})")
        if lost != elost:
            ok = False
            print("  FAIL: lost-cluster mismatch")
        else:
            print("  OK: lost-cluster count matches")
        # backup-FAT (mirror) mismatch
        fatmis = sim.u32(sym["v_fatmis"])
        print(f"  fatmis={fatmis} (exp 0, FAT0==FAT1 on this image)")
        if fatmis != 0:
            ok = False
            print("  FAIL: unexpected FAT-mirror mismatch")
        else:
            print("  OK: backup FAT matches primary")
    except Exception as e:
        ok = False
        print(f"  {type(e).__name__}: {e}")

    # --- Test 4: repair (free lost clusters) on a writable COPY ---
    print("=== repair: free lost clusters on a writable copy ===")
    try:
        import shutil
        copy = HERE / "test_repair.img"
        shutil.copy(IMG, copy)
        ef0, efb0, ed0, edb0 = expected_walk(copy)     # tree before
        free0, _ = expected_free(copy)
        reach0 = reachable_set(copy)                    # clusters that must survive

        def read_fat(p):
            with open(p, "rb") as f:
                d = f.read(512)
                resv = struct.unpack_from("<H", d, 14)[0]
                fatsz = struct.unpack_from("<I", d, 36)[0]
                f.seek(resv * 512)
                return f.read(fatsz * 512)
        fatA = read_fat(copy)
        sim = Sim(wmf, img=copy, writable=True)
        sim.reg.PC = sym["PLUGIN"]; sim.reg.SP = 0xBF00
        sim.run_until_pc(sym["PLUGIN.wait"], max_steps=20_000_000)
        lost_before = sim.u32(sym["v_lost"])
        sim.call(sym["repair_lost"], max_steps=20_000_000)
        recovered = sim.u32(sym["v_recovered"])
        sim.sdf.flush()
        ef1, efb1, ed1, edb1 = expected_walk(copy)      # tree after (re-read written FAT)
        free1, _ = expected_free(copy)
        lost1 = expected_lost(copy)
        with open(copy, "rb") as f:
            d = f.read(512)
            resv = struct.unpack_from("<H", d, 14)[0]
            fatsz = struct.unpack_from("<I", d, 36)[0]
            f.seek(resv * 512); fat0 = f.read(fatsz * 512); fat1 = f.read(fatsz * 512)
        print(f"  lost_before={lost_before}  recovered={recovered}")
        print(f"  tree before files={ef0} dirs={ed0} fb={efb0}  after files={ef1} dirs={ed1} fb={efb1}")
        print(f"  free before={free0} after={free1} (expect +{lost_before})  lost after={lost1} (expect 0)")
        # rigorous: every reachable cluster's FAT entry must be UNCHANGED;
        # every changed entry must have gone nonzero->0 and be unreachable.
        fatB = read_fat(copy)
        reach_unchanged = all(
            fatA[c * 4:c * 4 + 4] == fatB[c * 4:c * 4 + 4] for c in reach0)
        changed = [c for c in range(2, len(fatA) // 4)
                   if fatA[c * 4:c * 4 + 4] != fatB[c * 4:c * 4 + 4]]
        only_orphans_zeroed = all(
            c not in reach0
            and struct.unpack_from("<I", fatA, c * 4)[0] != 0
            and struct.unpack_from("<I", fatB, c * 4)[0] == 0
            for c in changed)
        print(f"  reachable clusters={len(reach0)}  changed entries={len(changed)}")
        checks = {
            "recovered==lost": recovered == lost_before,
            "tree intact": (ef1, efb1, ed1, edb1) == (ef0, efb0, ed0, edb0),
            "lost cleared": lost1 == 0,
            "free grew by recovered": free1 == free0 + lost_before,
            "FAT0==FAT1": fat0 == fat1,
            "reachable FAT unchanged": reach_unchanged,
            "only orphans zeroed": only_orphans_zeroed and len(changed) == recovered,
        }
        for name, good in checks.items():
            print(f"    [{'OK' if good else 'FAIL'}] {name}")
            if not good:
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
