#!/bin/bash
# package.sh -- assemble the numbered handoff tree and the zip (drop 0.14). Run after regress_full.sh.
set -e
cd "$(dirname "$0")/.."
P=AstraCore_Neo_handoff_v0.16
rm -rf "/home/claude/$P" && mkdir -p "/home/claude/$P"
D="/home/claude/$P"
mkdir -p "$D/00_overview" "$D/01_rtl" "$D/02_dv" "$D/03_models" "$D/04_software" "$D/05_safety" "$D/06_procurement" "$D/07_ci" "$D/logs"
cp handoff/HANDOFF_README.md "$D/README.md"
cp handoff/make_repo.sh "$D/"
cp handoff/overview/* "$D/00_overview/"
cp rtl/*.sv "$D/01_rtl/"
cp -r tb dv "$D/02_dv/"
find "$D/02_dv" -name "sim_build" -type d -exec rm -rf {} + 2>/dev/null || true
mkdir -p "$D/02_dv/sim" && cp sim/*.py sim/*.sh "$D/02_dv/sim/" 2>/dev/null || true
for v in vectors vectors_core vectors_tile; do [ -d "$v" ] && cp -r "$v" "$D/02_dv/"; done
cp -r model compiler "$D/03_models/"
cp sw/* "$D/04_software/"
cp safety/* "$D/05_safety/"; cp handoff/assessor_concept_review.md "$D/05_safety/"
cp handoff/ip_rfq_pack.md "$D/06_procurement/"
mkdir -p "$D/08_ip_product" && cp handoff/ip/* model/neo_ip_ppa.txt "$D/08_ip_product/"
mkdir -p "$D/09_synthesis" && cp syn/*.tcl syn/*.sdc syn/*.md "$D/09_synthesis/"
mkdir -p "$D/10_fpga" && cp fpga/* "$D/10_fpga/"
mkdir -p "$D/11_tool_qualification" && cp handoff/tool_qual/* compiler/tool_qual.py "$D/11_tool_qualification/"
cp Makefile "$D/07_ci/"; cp .github/workflows/regress.yml "$D/07_ci/"
cp logs/*.log "$D/logs/"
# older per-test logs kept in sim/ are superseded by logs/; drop caches
find "$D" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
find "$D" -name "*.pyc" -delete 2>/dev/null || true
( cd /home/claude && rm -f "$P.zip" && zip -qr "$P.zip" "$P" )
ls -la "/home/claude/$P.zip"
find "$D" -type f | wc -l
