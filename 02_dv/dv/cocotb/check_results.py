#!/usr/bin/env python3
"""check_results.py -- summarize cocotb results files (drop 0.20; skipped tests shown since drop 0.24).
cocotb 1.x's make returns 0 even when tests fail; this reads one or more results.xml files, prints one table and
exits nonzero when any test failed or a file is missing, so the CI step carries the verdict.
  python3 dv/cocotb/check_results.py dv/cocotb/results.xml dv/cocotb/results_tile8.xml
"""
import os
import sys
import xml.etree.ElementTree as ET


def main(paths):
    rows, bad = [], 0
    for p in paths:
        if not os.path.exists(p):
            rows.append((p, "-", "MISSING", "-"))
            bad += 1
            continue
        for tc in ET.parse(p).getroot().iter("testcase"):
            failed = tc.find("failure") is not None
            skipped = tc.find("skipped") is not None
            bad += failed
            rows.append((os.path.basename(p), tc.get("name"), "FAIL" if failed else ("SKIP" if skipped else "PASS"), tc.get("time", "-")))
    w = max(len(r[1]) for r in rows) if rows else 10
    print("cocotb results")
    for f, name, st, t in rows:
        print(f"  {f:<20} {name:<{w}}  {st:<7} {t} s")
    n_pass = sum(1 for r in rows if r[2] == "PASS")
    n_skip = sum(1 for r in rows if r[2] == "SKIP")
    print(f"  {n_pass} passed, {bad} failed/missing, {n_skip} skipped, {len(rows)} total")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["results.xml"]))
