#!/usr/bin/env python3
"""coverage_report.py -- the coverage report (drop 0.26, item 5).
Reads Verilator coverage data files (written by a --coverage build: one point per source line and per toggled signal
bit, with its hit count) and the functional-coverage YAML files the cocotb tests export (cocotb-coverage covergroups),
and writes one Markdown report: line and toggle coverage per RTL file, the uncovered line points with their sources
(so a reviewer can see what no test reached), and every covergroup with its percentage and empty bins.
  python3 dv/cocotb/coverage_report.py --dat dv/cocotb/coverage_*.dat --funcov dv/cocotb/funcov_*.yml --out report.md
Data files are optional (a missing kind of coverage is reported as absent, never as zero).
"""
import argparse
import glob
import os
import re
import sys
from collections import defaultdict


def parse_dat(path):
    """Verilator coverage.dat: lines `C '<key\\x02value\\x01...>' <count>`; the page key tells the kind (v_line, v_toggle, ...)."""
    points = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.startswith("C '"):
                continue
            body, _, count = line[3:].rpartition("' ")
            try:
                count = int(count.strip())
            except ValueError:
                continue
            kv = {}
            for item in body.split("\x01"):
                if "\x02" in item:
                    k, v = item.split("\x02", 1)
                    kv[k] = v
            if "f" not in kv:
                continue
            page = kv.get("page", "")
            kind = page.split("/")[0][2:] if page.startswith("v_") else kv.get("t", "other")   # v_line -> line, v_toggle -> toggle
            points.append((kv["f"], kind, int(kv.get("l", "0") or 0), kv.get("n", ""), count))
    return points


def source_line(path, lineno, roots):
    for r in roots:
        p = os.path.join(r, path) if r else path
        if os.path.exists(p):
            try:
                with open(p) as f:
                    for i, l in enumerate(f, 1):
                        if i == lineno:
                            return l.rstrip()
            except OSError:
                return ""
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dat", nargs="*", default=[], help="Verilator coverage data files (globs allowed)")
    ap.add_argument("--funcov", nargs="*", default=[], help="cocotb-coverage YAML exports (globs allowed)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--roots", nargs="*", default=["", "..", "../..", "../../.."], help="where to look for the RTL sources")
    ap.add_argument("--only", default="rtl/", help="report files whose path contains this (default: the RTL)")
    ap.add_argument("--max-uncovered", type=int, default=40, help="uncovered line points listed per file")
    a = ap.parse_args()
    dats = sorted({p for g in a.dat for p in glob.glob(g)})
    fcs = sorted({p for g in a.funcov for p in glob.glob(g)})
    out = ["# Coverage report", ""]
    out.append(f"Sources: {len(dats)} Verilator coverage file(s) ({', '.join(os.path.basename(d) for d in dats) or 'none'}), "
               f"{len(fcs)} functional-coverage file(s) ({', '.join(os.path.basename(f) for f in fcs) or 'none'}).")
    out.append("")
    # ---- structural: merge every data file, a point is covered when any run hit it ----
    merged = defaultdict(int)           # (file, kind, line, name) -> max count
    for d in dats:
        for f, kind, line, name, count in parse_dat(d):
            if a.only and a.only not in f:
                continue
            key = (f, kind, line, name)
            merged[key] = max(merged[key], count)
    if merged:
        per_file = defaultdict(lambda: defaultdict(lambda: [0, 0]))   # file -> kind -> [covered, total]
        for (f, kind, line, name), count in merged.items():
            per_file[f][kind][1] += 1
            per_file[f][kind][0] += 1 if count > 0 else 0
        kinds = sorted({k for f in per_file.values() for k in f})
        out.append("## Line and toggle coverage per RTL file (Verilator --coverage, every test of the build)")
        out.append("")
        out.append("| File | " + " | ".join(f"{k} covered / points | {k} %" for k in kinds) + " |")
        out.append("| --- | " + " | ".join("---: | ---:" for _ in kinds) + " |")
        totals = defaultdict(lambda: [0, 0])
        for f in sorted(per_file):
            cells = []
            for k in kinds:
                c, t = per_file[f].get(k, [0, 0])
                totals[k][0] += c; totals[k][1] += t
                cells.append(f"{c} / {t} | {100.0 * c / t:.1f}" if t else "— | —")
            out.append(f"| {os.path.basename(f)} | " + " | ".join(cells) + " |")
        cells = []
        for k in kinds:
            c, t = totals[k]
            cells.append(f"{c} / {t} | {100.0 * c / t:.1f}" if t else "— | —")
        out.append("| **all** | " + " | ".join(cells) + " |")
        out.append("")
        out.append("## Uncovered line points (what no test reached)")
        out.append("")
        for f in sorted(per_file):
            unc = sorted({(line, name) for (ff, kind, line, name), count in merged.items() if ff == f and kind == "line" and count == 0})
            if not unc:
                continue
            out.append(f"### {os.path.basename(f)} — {len(unc)} uncovered line point(s)")
            out.append("")
            for line, name in unc[:a.max_uncovered]:
                src = source_line(f, line, a.roots).strip()
                out.append(f"- line {line}: `{src[:110]}`" if src else f"- line {line} ({name})")
            if len(unc) > a.max_uncovered:
                out.append(f"- … and {len(unc) - a.max_uncovered} more")
            out.append("")
    else:
        out.append("## Line and toggle coverage: no Verilator coverage data found (build with COVERAGE=1)")
        out.append("")
    # ---- functional: cocotb-coverage YAML ----
    out.append("## Functional coverage (cocotb-coverage covergroups sampled by the tests)")
    out.append("")
    if fcs:
        try:
            import yaml
        except ImportError:
            yaml = None
        if yaml is None:
            out.append("pyyaml is not installed: the functional-coverage files could not be read.")
        else:
            merged_fc = {}                      # covergroup -> {bin: hit}  (union over the files: a bin counts when any build hit it)
            out.append("| File | Covergroup | Coverage | Bins hit / bins | Empty bins |")
            out.append("| --- | --- | ---: | ---: | --- |")
            for fp in fcs:
                try:
                    with open(fp) as f:
                        db = yaml.safe_load(f) or {}
                except Exception as e:  # noqa: BLE001
                    out.append(f"| {os.path.basename(fp)} | (unreadable: {e}) | | | |")
                    continue
                for name, info in sorted(db.items()):
                    if not isinstance(info, dict) or "cover_percentage" not in info:
                        continue
                    hits = info.get("bins:_hits") or info.get("bins_hits") or {}
                    empty = [str(b) for b, h in hits.items() if not h] if isinstance(hits, dict) else []
                    if isinstance(hits, dict) and hits:
                        m = merged_fc.setdefault(name, {})
                        for b, h in hits.items():
                            m[str(b)] = m.get(str(b), 0) + (h or 0)
                    out.append(f"| {os.path.basename(fp)} | {name} | {float(info['cover_percentage']):.1f} % | "
                               f"{info.get('coverage', '?')} / {info.get('size', '?')} | {', '.join(empty) if empty else '—'} |")
            leaves = {n: b for n, b in merged_fc.items() if not any(o != n and o.startswith(n + ".") for o in merged_fc)}
            if leaves:
                out.append("")
                out.append("### Merged over all files (a bin counts when any build hit it)")
                out.append("")
                out.append("| Covergroup | Coverage | Bins hit / bins | Empty bins |")
                out.append("| --- | ---: | ---: | --- |")
                tot_hit = tot_bins = 0
                for name in sorted(leaves):
                    b = leaves[name]
                    hit = sum(1 for h in b.values() if h)
                    tot_hit += hit; tot_bins += len(b)
                    empty = [k for k, h in b.items() if not h]
                    out.append(f"| {name} | {100.0 * hit / len(b):.1f} % | {hit} / {len(b)} | {', '.join(empty) if empty else '—'} |")
                out.append(f"| **all leaf covergroups** | {100.0 * tot_hit / tot_bins:.1f} % | {tot_hit} / {tot_bins} | |")
    else:
        out.append("No functional-coverage files found (the tests export them when NEO_FUNCOV=1 and cocotb-coverage is installed).")
    out.append("")
    with open(a.out, "w") as f:
        f.write("\n".join(out))
    print("\n".join(out[:4]))
    print(f"coverage report written to {a.out} ({len(out)} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
