#!/usr/bin/env python3
"""check_c_runtime.py -- the C runtime (sw/neo_runtime.c over a recording device, sw/test_runtime.c) must issue exactly the
transactions of the Python runtime (sw/neo_run.py) on the same network image: the two-layer network of the top suite's T9
and the YOLOv8-m group of T10, in the command-queue mode and the window mode, with and without telemetry. Comments ('#')
are not compared; every other line (register writes, reads with their tags, polls, host-bridge and memory loads word for
word, read-backs) must be identical."""
import os, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "compiler"))
sys.path.insert(0, os.path.join(HERE, "..", "sw"))
import neo_run  # noqa: E402

build = os.path.join(HERE, "..", "build")
os.makedirs(build, exist_ok=True)
exe = os.path.join(build, "test_runtime")
fails, checked = 0, 0
for name in ("two_layer", "yolo"):
    img = neo_run.BUILDERS[name]()
    path = os.path.join(build, f"rt_{name}.img")
    img.write(path)
    for mode in ("queue", "window"):
        for tel in (True, False):
            dev = neo_run.TraceDevice()
            neo_run.Runtime(dev, img, mode=mode, telemetry=tel).run()
            py = [ln for ln in dev.lines if not ln.startswith("#")]
            r = subprocess.run([exe, path, mode] + ([] if tel else ["notel"]), capture_output=True, text=True)
            c = [ln for ln in r.stdout.splitlines() if not ln.startswith("#")]
            checked += 1
            ok = r.returncode == 0 and c == py
            if not ok:
                fails += 1
                first = next((i for i in range(min(len(c), len(py))) if c[i] != py[i]), min(len(c), len(py)))
                print(f"MISMATCH {name} {mode} telemetry={tel}: C {len(c)} lines, Python {len(py)}; first difference at line {first}: "
                      f"C '{c[first][:80] if first < len(c) else '<end>'}' vs Python '{py[first][:80] if first < len(py) else '<end>'}'"
                      + (f"; C exited {r.returncode}: {r.stderr.strip()[-200:]}" if r.returncode else ""))
            else:
                print(f"{name:10s} {mode:6s} telemetry={tel!s:5s}: {len(py)} transactions identical")
print(f"C runtime vs Python runtime: {checked} traces compared -> {'ALL PASS' if fails == 0 else f'{fails} FAILURES'}")
raise SystemExit(1 if fails else 0)
