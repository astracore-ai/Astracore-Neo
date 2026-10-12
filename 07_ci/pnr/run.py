#!/usr/bin/env python3
"""run.py -- one block of the place-and-route job through OpenROAD-flow-scripts (drop 0.42, stream A2b).

From synth.py's output for the unit (the job runs `synth.py --blocks <unit> --period <P>` first):
  1. fakeram.py: a FakeRAM2.0 LEF + LIB per memory-leaf type of the subtree and the leaves' wrappers (in/macros/)
  2. prep.py: the subtree's netlists as one hierarchical netlist with tie cells (in/1_netlist.v) and the unit's
     block.sdc (in/1_block.sdc), the macro boundary checked
  3. the same netlist flat in OpenSTA with the same SDC, ideal clock, no wires (in/synth_sta/): the synthesized
     estimate the routed figures are divided by, on exactly the netlist ORFS places (synth.py's own per-unit figures,
     children as timing models, are kept beside it in prep.json)
  4. ORFS, one make target per stage -- synth (the netlist copied and linked: 1_synth.odb), floorplan (incl. the macro
     placer, tap cells, PDN), place, cts, route, do-finish (fill, OpenRCX, the final report) -- in the docker image
     tools.sh pulled, the workspace mounted at its own path, as the runner's user; the time left of --budget-min bounds
     every stage (a stage cut short is reported as such, the job still publishes)
  5. neo_report.tcl on the final database (or the last one a failed run left)
  6. the logs, reports and metrics JSON of every stage, run.json (stage times, exit codes, the image, the make
     arguments) and the inputs' records into --logs (ci_logs/pnr/<block>/), then report.py -> pnr_<block>.md there
Exit status 0 when every stage and the report ran, 1 otherwise.

--runner docker (CI), local (ORFS installed: FLOW_HOME or --flow-home) or dry (print the commands: the sandbox).
"""
import argparse
import glob
import json
import os
import shlex
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STAGES = ["synth", "floorplan", "place", "cts", "route", "do-finish"]
# the database a stage leaves, newest first: neo_report.tcl reads the last one there is
STAGE_DB = [("6_final.odb", "6_final.sdc"), ("5_route.odb", "5_route.sdc"), ("4_cts.odb", "4_cts.sdc"),
            ("3_place.odb", "3_place.sdc"), ("2_floorplan.odb", "2_floorplan.sdc")]
ASAP7_SS = ["asap7sc7p5t_AO_RVT_SS_nldm_211120.lib", "asap7sc7p5t_INVBUF_RVT_SS_nldm_220122.lib",
            "asap7sc7p5t_OA_RVT_SS_nldm_211120.lib", "asap7sc7p5t_SIMPLE_RVT_SS_nldm_211120.lib",
            "asap7sc7p5t_SEQ_RVT_SS_nldm_220123.lib"]

SYNTH_STA_TCL = """# {unit}: prep.py's netlist flat in OpenSTA, block.sdc, ideal clock, no wire load -- the synthesized estimate on the
# netlist ORFS places (07_ci/pnr/run.py)
{read_libs}
read_verilog {netlist}
link_design {unit}
read_sdc {sdc}
set f [open summary.txt w]
puts $f "wns_max [worst_slack -max]"
puts $f "tns_max [total_negative_slack -max]"
puts $f "wns_min [worst_slack -min]"
puts $f "instances [llength [get_cells -hier *]]"
close $f
report_clock_min_period > min_period.txt
report_clock_min_period -include_port_paths > min_period_ports.txt
report_checks -path_delay max -group_path_count 10 -endpoint_path_count 1 -unique_paths_to_endpoint -fields {{slew cap fanout}} -digits 1 -format full_clock_expanded > paths_max.txt
report_checks -path_delay max -from [all_registers] -to [all_registers] -group_path_count 5 -endpoint_path_count 1 -digits 1 -format full_clock_expanded > paths_reg2reg.txt
exit
"""


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def sh(cmd, logfile=None, timeout=None, cwd=None):
    """Run cmd (a list), its output into logfile (and the console when there is none); returns (exit, seconds)."""
    t0 = time.time()
    try:
        if logfile:
            with open(logfile, "a") as f:
                f.write("$ " + " ".join(shlex.quote(c) for c in cmd) + "\n")
                f.flush()
                rc = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, timeout=timeout, cwd=cwd).returncode
        else:
            rc = subprocess.run(cmd, timeout=timeout, cwd=cwd).returncode
    except subprocess.TimeoutExpired:
        rc = 124
    return rc, time.time() - t0


class Orfs:
    """make in the ORFS flow directory, in the docker image or a local install."""

    def __init__(self, args, block, work, in_dir):
        self.args, self.block, self.work, self.in_dir = args, block, work, in_dir
        self.config = os.path.join(HERE, "designs", block, "config.mk")
        self.flow_home = "/OpenROAD-flow-scripts/flow" if args.runner == "docker" else args.flow_home
        self.extra = shlex.split(args.flow_args or "")

    def make_cmd(self, target, extra_vars=()):
        mk = ["make", "-f", os.path.join(self.flow_home, "Makefile"), f"DESIGN_CONFIG={self.config}",
              f"WORK_HOME={self.work}", f"NEO_PNR_IN={self.in_dir}"] + self.extra + list(extra_vars) + [target]
        if self.args.runner != "docker":
            return mk, None
        name = f"neo-pnr-{self.block}-{target.replace('/', '_')}-{os.getpid()}"
        mount = self.args.mount
        cmd = ["docker", "run", "--rm", "--init", "--name", name, "-u", f"{os.getuid()}:{os.getgid()}",
               "-e", "HOME=/tmp", "-v", f"{mount}:{mount}", "-w", self.work, self.args.image] + mk
        return cmd, name

    def run(self, target, logfile, timeout, extra_vars=()):
        cmd, name = self.make_cmd(target, extra_vars)
        if self.args.runner == "dry":
            print("DRY:", " ".join(shlex.quote(c) for c in cmd))
            return 0, 0.0
        rc, secs = sh(cmd, logfile, timeout=timeout, cwd=self.work)
        if rc == 124 and name:                      # the budget ran out: the container goes with the make
            subprocess.run(["docker", "kill", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return rc, secs


def synth_sta(args, unit, in_dir, macros_dir):
    """OpenSTA on the prepared netlist: the like-for-like synthesized figures (skipped without an sta binary)."""
    sta = args.sta or os.environ.get("NEO_STA") or os.path.join(os.environ.get("NEO_SYNTH_TOOLS", "/opt/neo-synth"), "sta")
    if not (os.path.isfile(sta) and os.access(sta, os.X_OK)):
        log(f"{unit}: no OpenSTA ({sta}): the flat synthesized estimate is skipped")
        return None
    d = os.path.join(in_dir, "synth_sta")
    os.makedirs(d, exist_ok=True)
    libs = [os.path.join(args.lib_dir, f) for f in ASAP7_SS] + sorted(glob.glob(os.path.join(macros_dir, "*.lib")))
    with open(os.path.join(d, "sta.tcl"), "w") as f:
        f.write(SYNTH_STA_TCL.format(unit=unit, read_libs="\n".join(f"read_liberty {l}" for l in libs),
                                     netlist=os.path.join(in_dir, "1_netlist.v"), sdc=os.path.join(in_dir, "1_block.sdc")))
    rc, secs = sh([sta, "-no_init", "-no_splash", "-exit", "sta.tcl"], os.path.join(d, "sta.log"), timeout=3600, cwd=d)
    log(f"{unit}: flat synthesized STA exit {rc} in {secs:.0f} s")
    return rc


def collect(work, block, logs, in_dir):
    """The text outputs of ORFS (logs, reports, metrics) and the inputs' records into the logs directory."""
    n = 0
    for kind in ("logs", "reports"):
        src = os.path.join(work, kind, "asap7", block, "base")
        if not os.path.isdir(src):
            continue
        for root, _, files in os.walk(src):
            for fn in files:
                p = os.path.join(root, fn)
                if os.path.getsize(p) > 50 * 1024 * 1024 or fn.endswith((".odb", ".def", ".gds", ".spef", ".v.gz")):
                    continue
                rel = os.path.relpath(p, src)
                dst = os.path.join(logs, "orfs", kind, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy(p, dst)
                n += 1
    res = os.path.join(work, "results", "asap7", block, "base")
    for fn in ("2_2_floorplan_macro.tcl", "1_synth.sdc", "6_final.sdc"):
        p = os.path.join(res, fn)
        if os.path.isfile(p):
            os.makedirs(os.path.join(logs, "orfs", "results"), exist_ok=True)
            shutil.copy(p, os.path.join(logs, "orfs", "results", fn))
    for p in ["prep.json", "prep.log", "prep.ys", "check.txt", "stat.txt", "1_block.sdc"]:
        if os.path.isfile(os.path.join(in_dir, p)):
            os.makedirs(os.path.join(logs, "in"), exist_ok=True)
            shutil.copy(os.path.join(in_dir, p), os.path.join(logs, "in", p))
    for p in glob.glob(os.path.join(in_dir, "macros", "*")):
        if p.endswith((".json", ".v", ".lef", ".lib", ".log")):
            os.makedirs(os.path.join(logs, "in", "macros"), exist_ok=True)
            shutil.copy(p, os.path.join(logs, "in", "macros", os.path.basename(p)))
    for p in glob.glob(os.path.join(in_dir, "synth_sta", "*")):
        os.makedirs(os.path.join(logs, "in", "synth_sta"), exist_ok=True)
        shutil.copy(p, os.path.join(logs, "in", "synth_sta", os.path.basename(p)))
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--block", required=True, help="a synth.py unit with a designs/<block>/config.mk")
    ap.add_argument("--synth", required=True, help="synth.py's output directory")
    ap.add_argument("--out", required=True, help="the working directory (in/ and orfs/ per block)")
    ap.add_argument("--logs", required=True, help="where the block's logs and report go (ci_logs/pnr/<block>)")
    ap.add_argument("--runner", choices=("docker", "local", "dry"), default="docker")
    ap.add_argument("--image", default=os.environ.get("NEO_ORFS_IMAGE", ""), help="the ORFS docker image (tools.sh)")
    ap.add_argument("--flow-home", default=os.environ.get("FLOW_HOME", ""), help="ORFS flow/ for --runner local")
    ap.add_argument("--mount", default=os.getcwd(), help="the directory mounted into the container at its own path")
    ap.add_argument("--fakeram", default=os.path.join(os.environ.get("NEO_PNR_TOOLS", "/opt/neo-pnr"), "FakeRAM2.0"))
    ap.add_argument("--lib-dir", default=os.path.join(os.environ.get("NEO_PDK_LIB") or
                                                      os.path.join(os.environ.get("NEO_SYNTH_TOOLS", "/opt/neo-synth"), "pdk", "lib"), "asap7"))
    ap.add_argument("--yosys", default=os.environ.get("NEO_YOSYS") or
                    os.path.join(os.environ.get("NEO_SYNTH_TOOLS", "/opt/neo-synth"), "oss-cad-suite", "bin", "yosys"))
    ap.add_argument("--sta", default="")
    ap.add_argument("--flow-args", default="", help="extra make variables for ORFS, e.g. 'TNS_END_PERCENT=5'")
    ap.add_argument("--budget-min", type=float, default=300.0, help="minutes for the whole block (the job's limit is 350)")
    ap.add_argument("--stages", default=",".join(STAGES), help="the ORFS targets to run, in order")
    args = ap.parse_args()
    t_start = time.time()
    block = args.block
    if not os.path.isfile(os.path.join(HERE, "designs", block, "config.mk")):
        sys.exit(f"run.py: no design {block} (designs: {sorted(os.listdir(os.path.join(HERE, 'designs')))})")
    if args.runner == "docker" and not args.image:
        sys.exit("run.py: --image (or NEO_ORFS_IMAGE) is required with --runner docker")
    out = os.path.abspath(args.out)
    logs = os.path.abspath(args.logs)
    base = os.path.join(out, block)
    in_dir, work = os.path.join(base, "in"), os.path.join(base, "orfs")
    for d in (in_dir, work, logs):
        os.makedirs(d, exist_ok=True)
    args.mount = os.path.abspath(args.mount)
    for p in (in_dir, work, HERE):
        if os.path.commonpath([args.mount, p]) != args.mount and args.runner == "docker":
            sys.exit(f"run.py: {p} is outside the mounted directory {args.mount}")
    record = dict(block=block, runner=args.runner, image=args.image, flow_args=args.flow_args, budget_min=args.budget_min,
                  start=time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()), stages=[], status="started")

    def save():
        record["seconds"] = round(time.time() - t_start, 1)
        with open(os.path.join(logs, "run.json"), "w") as f:
            json.dump(record, f, indent=1)

    save()
    py = sys.executable
    macros_dir = os.path.join(in_dir, "macros")
    rc, secs = sh([py, os.path.join(HERE, "fakeram.py"), "--synth", args.synth, "--unit", block, "--fakeram", args.fakeram,
                   "--out", macros_dir])
    record["stages"].append(dict(stage="fakeram", exit=rc, seconds=round(secs, 1)))
    if rc != 0:
        record["status"] = "fakeram failed"
        save()
        return 1
    libs = [os.path.join(args.lib_dir, f) for f in ASAP7_SS]
    rc, secs = sh([py, os.path.join(HERE, "prep.py"), "--synth", args.synth, "--unit", block, "--macros", macros_dir,
                   "--libs"] + libs + ["--yosys", args.yosys, "--out", in_dir])
    record["stages"].append(dict(stage="prep", exit=rc, seconds=round(secs, 1)))
    if rc != 0:
        record["status"] = "prep failed"
        collect(work, block, logs, in_dir)
        save()
        return 1
    rc = synth_sta(args, block, in_dir, macros_dir)
    record["stages"].append(dict(stage="synth_sta", exit=rc))
    save()

    orfs = Orfs(args, block, work, in_dir)
    budget = args.budget_min * 60.0
    failed = None
    for stage in [s for s in args.stages.split(",") if s]:
        left = budget - (time.time() - t_start)
        if left < 60:
            failed = f"budget exhausted before {stage}"
            record["stages"].append(dict(stage=stage, exit=None, seconds=0, note="not run: the budget is spent"))
            break
        log(f"{block}: ORFS {stage} ({left / 60:.0f} min left)")
        rc, secs = orfs.run(stage, os.path.join(logs, f"orfs_{stage}.txt"), timeout=left)
        record["stages"].append(dict(stage=stage, exit=rc, seconds=round(secs, 1), note="timeout" if rc == 124 else ""))
        save()
        log(f"{block}: ORFS {stage} exit {rc} in {secs / 60:.1f} min")
        if rc != 0:
            failed = f"{stage} {'timed out' if rc == 124 else 'failed'} (exit {rc})"
            break
    # the report on the newest database there is (after a failure too)
    res = os.path.join(work, "results", "asap7", block, "base")
    db = next(((o, s) for o, s in STAGE_DB if os.path.isfile(os.path.join(res, o)) and os.path.isfile(os.path.join(res, s))), None)
    if args.runner == "dry":
        db = STAGE_DB[0]
    if db:
        left = max(600.0, budget + 15 * 60 - (time.time() - t_start))     # the report gets up to 15 min past the budget
        rc, secs = orfs.run("run", os.path.join(logs, "orfs_neo_report.txt"), timeout=left,
                            extra_vars=[f"RUN_SCRIPT={os.path.join(HERE, 'neo_report.tcl')}", "RUN_LOG_NAME_STEM=7_neo_report",
                                        f"NEO_REPORT_ODB={db[0]}", f"NEO_REPORT_SDC={db[1]}"])
        record["stages"].append(dict(stage="neo_report", exit=rc, seconds=round(secs, 1), db=db[0]))
        if rc != 0 and not failed:
            failed = f"neo_report failed (exit {rc})"
    record["status"] = failed or "complete"
    n = collect(work, block, logs, in_dir)
    record["collected_files"] = n
    save()
    rc, _ = sh([py, os.path.join(HERE, "report.py"), "--logs", logs, "--block", block])
    log(f"{block}: {record['status']}; report.py exit {rc}; {n} files in {logs}")
    return 0 if record["status"] == "complete" and rc == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
