#!/usr/bin/env python3
"""JARVIS-VLSI simulation automation: compile, run, and verify Icarus Verilog designs."""

import argparse
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_SOURCES = ["bitnet_ternary_pe.v", "async_fifo_cdc.v", "tb_unified_suite.v"]
FALLBACK_IVERILOG = [
    r"C:\iverilog\bin\iverilog.exe",
    r"C:\Program Files\iverilog\bin\iverilog.exe",
]


def find_tool(name: str) -> str:
    path = shutil.which(name)
    if path:
        return path
    for candidate in FALLBACK_IVERILOG:
        exe = Path(candidate)
        if not exe.exists():
            continue
        if exe.stem == name:
            return str(exe)
        sibling = exe.with_name(f"{name}.exe")
        if sibling.exists():
            return str(sibling)
    raise FileNotFoundError(f"{name} not found on PATH; install Icarus Verilog first.")


def run(cmd: list[str], cwd: Path) -> tuple[int, str]:
    proc = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=300
    )
    return proc.returncode, (proc.stdout + proc.stderr)


def compile_design(iverilog: str, sources: list[str], output: Path, cwd: Path) -> tuple[bool, str]:
    cmd = [iverilog, "-o", output.name, *sources]
    rc, log = run(cmd, cwd)
    return rc == 0, log


def run_simulation(vvp: str, output: Path, cwd: Path) -> tuple[bool, str]:
    cmd = [vvp, output.name]
    rc, log = run(cmd, cwd)
    passed = rc == 0 and "[VERIFICATION PASSED]" in log and "[VERIFICATION FAILED]" not in log
    return passed, log


def parse_stats(log: str) -> dict:
    stats = {
        "errors": len(re.findall(r"\[FAIL\]", log)),
        "passed": "[VERIFICATION PASSED]" in log,
        "finish_time": None,
    }
    m = re.search(r"\$finish called at (\d+)", log)
    if m:
        stats["finish_time"] = int(m.group(1))
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="JARVIS-VLSI RTL simulation runner")
    parser.add_argument("--sources", nargs="+", default=DEFAULT_SOURCES, help="Verilog source files")
    parser.add_argument("--wave", action="store_true", help="Open GTKWave on sim_output.vcd after a passing run")
    parser.add_argument("--keep", action="store_true", help="Keep sim.vvp build artifact")
    args = parser.parse_args()

    cwd = PROJECT_DIR
    missing = [s for s in args.sources if not (cwd / s).exists()]
    if missing:
        print(f"[ERROR] Missing source file(s): {', '.join(missing)}")
        return 2

    try:
        iverilog = find_tool("iverilog")
        vvp = find_tool("vvp")
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}")
        return 2

    output = cwd / "sim.vvp"
    t0 = time.time()

    print(f"[1/3] Compiling {len(args.sources)} file(s) with {iverilog} ...")
    ok, compile_log = compile_design(iverilog, args.sources, output, cwd)
    if not ok:
        print("[COMPILE FAILED]")
        print(compile_log)
        return 1
    if compile_log.strip():
        print(compile_log.strip())

    print("[2/3] Running simulation ...")
    ok, sim_log = run_simulation(vvp, output, cwd)
    print(sim_log.strip())

    print("[3/3] Results ...")
    stats = parse_stats(sim_log)
    elapsed = time.time() - t0
    status = "PASS" if ok and stats["errors"] == 0 else "FAIL"
    print(f"  Status        : {status}")
    print(f"  Failed checks : {stats['errors']}")
    if stats["finish_time"] is not None:
        print(f"  Sim end time  : {stats['finish_time']} ps")
    print(f"  Wall time     : {elapsed:.2f}s")

    if not args.keep and output.exists():
        output.unlink()

    if ok and stats["errors"] == 0 and args.wave:
        gtkwave = shutil.which("gtkwave")
        if gtkwave:
            vcd = cwd / "sim_output.vcd"
            if vcd.exists():
                subprocess.Popen([gtkwave, str(vcd)])
                print("  Waveform      : opened sim_output.vcd in GTKWave")
            else:
                print("  Waveform      : sim_output.vcd not found")
        else:
            print("  Waveform      : gtkwave not found on PATH")

    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
