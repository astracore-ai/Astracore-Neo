#!/usr/bin/env python3
"""run.py -- the formal job's runner (drop 0.41).

Runs every SymbiYosys file <dir>/*.sby (02_dv/formal in the repository, formal/ in the flat layout), every task of each
(sby --dumptasks), one sby process per task with its own directory <out>/<file>[_<task>] (sby -f -d), at most --jobs at a
time, each under --timeout seconds (the task's whole session -- sby and the solvers it started -- is killed at the limit:
TIMEOUT). It reads each task's result from sby's status file and log -- PASS / FAIL / UNKNOWN / ERROR / TIMEOUT, the mode,
the depth, the engine, the induction length or bmc steps reached, the wall time, the failing properties and their traces
-- writes <out>/formal_report.md and, with --logs, copies the report, every task's logfile.txt and every trace (.vcd) into
that directory (ci_logs/formal in CI). Exit status 1 when any task is not PASS.

Run from the flat layout, the suite's bin on PATH:  python3 formal/run.py --jobs 2 --timeout 600   (make formal)
One task:  sby -f -d formal_out/<file>[_<task>] formal/<file>.sby [<task>]
A smtbmc prove task runs two solver processes (basecase and induction), the abc and bmc tasks one: --jobs 2 is up to four
solver processes -- right for the 4-core runner; on a shared 2-core machine use --jobs 1.
"""
import argparse
import datetime
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path


def sby_out(sby, args):
    return subprocess.run([sby] + args, capture_output=True, text=True, check=False).stdout


def describe(sbyfile):
    """What a .sby file proves: its first line, '# <name> -- <statement>' (the lines after it are the details)."""
    first = Path(sbyfile).read_text().splitlines()[0] if Path(sbyfile).stat().st_size else ""
    m = re.match(r"^#\s*\S+\s+--\s+(.*)$", first)
    return (m.group(1) if m else first.lstrip("# ")).strip()


def task_config(sby, sbyfile, task):
    """mode, depth and engine of one task, from sby's preprocessed configuration."""
    cfg = sby_out(sby, ["--dumpcfg", str(sbyfile)] + ([task] if task else []))
    mode, depth, engines, section = None, None, [], None
    for raw in cfg.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^\[(\w+)\]", line)
        if m:
            section = m.group(1)
            continue
        if section == "options":
            parts = line.split()
            if parts[0] == "mode":
                mode = parts[1]
            elif parts[0] == "depth":
                depth = int(parts[1])
        elif section == "engines":
            engines.append(line)
    if depth is None:
        depth = 20                                       # sby's default for bmc and prove
    return mode or "?", depth, "; ".join(engines) or "?"


def parse_log(workdir, depth):
    """Result details from a task directory: status, induction length / bmc steps, failures and traces."""
    res = {"status": None, "reach": "", "failed": [], "traces": []}
    status = Path(workdir) / "status"
    if status.exists():
        words = status.read_text().split()
        res["status"] = words[0] if words else None
    log = Path(workdir) / "logfile.txt"
    if not log.exists():
        return res
    text = log.read_text(errors="replace")
    steps = [int(s) for s in re.findall(r"Trying induction in step (\d+)", text)]
    if "successful proof by k-induction" in text and steps:
        res["reach"] = f"k = {depth - min(steps)}"
    elif re.search(r"engine_\d+: .*Property proved", text):
        res["reach"] = "proved (pdr)"
    else:
        checked = [int(s) for s in re.findall(r"Checking assertions in step (\d+)", text)]
        if checked:
            res["reach"] = f"{max(checked) + 1} steps"
    for m in re.finditer(r"summary:\s+failed assertion (\S+) at (\S+)(?: step (\d+))?", text):
        where = f"{m.group(2)} ({m.group(1)})" + (f" at step {m.group(3)}" if m.group(3) else "")
        if where not in res["failed"]:
            res["failed"].append(where)
    for m in re.finditer(r"summary:\s+and (\d+) further assertions", text):
        res["failed"].append(f"and {m.group(1)} further assertions")
    for m in re.finditer(r"summary: counterexample trace(?: \[(\w+)\])?: (\S+)", text):
        res["traces"].append(m.group(2) + (f" ({m.group(1)})" if m.group(1) else ""))
    return res


def session_pids(sid):
    """The processes of session sid (sby is started as a session leader; it puts every engine in a process group of
    its own -- os.setpgrp -- but they stay in its session, so the session is what holds the whole task)."""
    pids = []
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open(f"/proc/{d}/stat") as f:
                stat = f.read()
            if int(stat[stat.rindex(")") + 2:].split()[3]) == sid:
                pids.append(int(d))
        except (OSError, ValueError, IndexError):
            continue
    return pids


def kill_session(sid):
    """SIGTERM then SIGKILL every process of the session; a solver must never outlive its task."""
    if not os.path.isdir("/proc"):                       # no procfs: the process group is the best there is
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(sid, sig)
            except (ProcessLookupError, PermissionError):
                return
            time.sleep(2)
        return
    for sig, grace in ((signal.SIGTERM, 5.0), (signal.SIGKILL, 1.0)):
        pids = session_pids(sid)
        if not pids:
            return
        for p in pids:
            try:
                os.kill(p, sig)
            except (ProcessLookupError, PermissionError):
                pass
        deadline = time.time() + grace
        while time.time() < deadline and session_pids(sid):
            time.sleep(0.2)


def run_task(sby, sbyfile, task, workdir, timeout):
    cmd = [sby, "-f", "-d", str(workdir), str(sbyfile)] + ([task] if task else [])
    t0 = time.time()
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, start_new_session=True)
    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
    kill_session(proc.pid)                               # at the limit, and any engine left running after sby
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass
    return time.time() - t0, timed_out, proc.returncode


def tool_versions(sby):
    """yosys -V and the suite's location (yosys next to sby, else on PATH)."""
    v = []
    yosys = Path(sby).parent / "yosys"
    yosys = str(yosys) if yosys.exists() else shutil.which("yosys")
    if yosys:
        v.append(subprocess.run([yosys, "-V"], capture_output=True, text=True, check=False).stdout.strip())
    v.append(f"sby, yosys-smtbmc, yices, yosys-abc from {Path(sby).parent}")
    return v


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", default="formal", help="directory of the .sby files (the flat layout's formal/)")
    ap.add_argument("--out", default="formal_out", help="task directories and formal_report.md")
    ap.add_argument("--logs", default=None, help="copy the report, the task logs and the traces here (ci_logs/formal)")
    ap.add_argument("--jobs", type=int, default=2, help="sby processes at a time")
    ap.add_argument("--timeout", type=int, default=600, help="seconds per task")
    ap.add_argument("--only", default="", help="comma-separated file or file_task names (default: all)")
    ap.add_argument("--sby", default=shutil.which("sby") or "sby")
    args = ap.parse_args()

    files = sorted(Path(args.dir).glob("*.sby"))
    if not files:
        print(f"run.py: no .sby files in {args.dir}", file=sys.stderr)
        return 1
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    only = {s for s in args.only.split(",") if s}
    jobs = []
    for f in files:
        tasks = sby_out(args.sby, ["--dumptasks", str(f)]).split() or [None]
        for t in tasks:
            name = f.stem + (f"_{t}" if t else "")
            if only and name not in only and f.stem not in only:
                continue
            mode, depth, engine = task_config(args.sby, f, t)
            jobs.append(dict(name=name, file=f, task=t, mode=mode, depth=depth, engine=engine, what=describe(f)))
    print(f"run.py: {len(jobs)} tasks from {len(files)} files, {args.jobs} at a time, {args.timeout} s each", flush=True)

    lock = threading.Lock()
    queue = list(jobs)

    def worker():
        while True:
            with lock:
                if not queue:
                    return
                j = queue.pop(0)
            wd = out / j["name"]
            elapsed, timed_out, rc = run_task(args.sby, j["file"], j["task"], wd, args.timeout)
            res = parse_log(wd, j["depth"])
            status = "TIMEOUT" if timed_out else (res["status"] or f"ERROR (sby rc {rc})")
            j.update(status=status, elapsed=elapsed, reach=res["reach"], failed=res["failed"], traces=res["traces"])
            with lock:
                print(f"  {status:8s} {j['name']:28s} {elapsed:7.1f} s  {j['mode']} {j['reach']}", flush=True)

    t0 = time.time()
    threads = [threading.Thread(target=worker) for _ in range(max(1, args.jobs))]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    total = time.time() - t0

    bad = [j for j in jobs if j["status"] != "PASS"]
    rows = []
    for j in jobs:
        depth = str(j["depth"]) if j["mode"] in ("bmc", "prove") else ""
        rows.append(f"| `{j['name']}` | {j['what']} | {j['mode']} | {depth} | `{j['engine']}` | "
                    f"{'**' + j['status'] + '**' if j['status'] != 'PASS' else 'PASS'} | {j['reach']} | {j['elapsed']:.1f} s |")
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [f"# Formal report -- SymbiYosys ({stamp})", "",
             "Tools: " + "; ".join(tool_versions(args.sby)) + ".", "",
             f"{len(jobs)} tasks, {len(jobs) - len(bad)} PASS, {len(bad)} not PASS; wall time {total:.0f} s with "
             f"{args.jobs} at a time, {args.timeout} s per task. Mode prove = a full proof (k-induction for smtbmc, the "
             "reached induction length k in the column; pdr / BDD collapse for abc); bmc = bounded to the depth given.", "",
             "| Task | What is proven | Mode | Depth | Engine | Result | Reached | Time |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"] + rows
    if bad:
        lines += ["", "## Not PASS", ""]
        for j in bad:
            lines.append(f"- `{j['name']}`: {j['status']}")
            for f in j["failed"]:
                lines.append(f"  - failed: {f}")
            for t in j["traces"]:
                lines.append(f"  - trace: `{t}`")
            lines.append(f"  - log: `{out / j['name'] / 'logfile.txt'}`")
    report = out / "formal_report.md"
    report.write_text("\n".join(lines) + "\n")
    print(f"run.py: {len(jobs) - len(bad)}/{len(jobs)} PASS in {total:.0f} s; report {report}", flush=True)

    if args.logs:
        logs = Path(args.logs)
        logs.mkdir(parents=True, exist_ok=True)
        shutil.copy(report, logs / "formal_report.md")
        for j in jobs:
            wd = out / j["name"]
            if (wd / "logfile.txt").exists():
                shutil.copy(wd / "logfile.txt", logs / f"{j['name']}.log")
            for vcd in sorted(wd.glob("engine_*/*.vcd")):
                shutil.copy(vcd, logs / f"{j['name']}_{vcd.parent.name}_{vcd.name}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
