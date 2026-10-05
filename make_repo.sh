#!/bin/bash
# make_repo.sh -- recreate the flat repository layout (what the Makefile and CI expect) from this package.
set -e
dest=${1:-neo_repo}
mkdir -p "$dest"
cp -r 01_rtl "$dest/rtl"
cp -r 02_dv/tb "$dest/tb"; cp -r 02_dv/dv "$dest/dv"; cp -r 02_dv/sim "$dest/sim"
for v in 02_dv/vectors*; do cp -r "$v" "$dest/$(basename "$v")"; done
cp -r 03_models/model "$dest/model"; cp -r 03_models/compiler "$dest/compiler"
cp -r 04_software "$dest/sw"
cp -r 05_safety "$dest/safety"
mkdir -p "$dest/handoff"; cp 06_procurement/* 05_safety/assessor_concept_review.md "$dest/handoff/" 2>/dev/null || true
cp 07_ci/Makefile "$dest/Makefile"; mkdir -p "$dest/.github/workflows"; cp 07_ci/regress.yml "$dest/.github/workflows/regress.yml"
cp -r logs "$dest/logs"; cp HANDOFF_README.md "$dest/README_HANDOFF.md"; cp 00_overview/* "$dest/" 2>/dev/null || true
echo "repository recreated in $dest; run: cd $dest && make regress"
