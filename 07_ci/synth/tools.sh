#!/bin/bash
# tools.sh -- the synthesis job's tools (drop 0.40), installed into $NEO_SYNTH_TOOLS (default /opt/neo-synth):
#   oss-cad-suite/   YosysHQ OSS CAD Suite nightly $OSS_DATE (yosys with the slang SystemVerilog front end, yosys-abc;
#                    GitHub release tarball, no apt)
#   sta              OpenSTA (parallaxsw, $STA_COMMIT) built from source (parallaxsw/OpenSTA) -- cmake, tcl-dev, swig, bison, flex,
#                    libeigen3-dev from apt on the runner, CUDD 3.0.0 from GitHub; the binary is what actions/cache keeps
#   pdk/lib/asap7    ASAP7 7.5-track RVT NLDM liberty files (SS, TT: AO, INVBUF, OA, SIMPLE, SEQ), gunzipped, and the
#                    platform's full-adder map; pdk/lib/nangate45: NangateOpenCellLibrary_typical.lib and its adder map;
#                    pdk/lib/common/lcu_kogge_stone.v -- all from OpenROAD-flow-scripts $ORFS_COMMIT (sparse checkout)
# Every version is pinned here; every download is a GitHub clone or release asset. Idempotent: a tool that is already
# there is kept (CI caches $NEO_SYNTH_TOOLS/sta between runs; the rest downloads in about a minute).
# The sandbox (no apt) builds Tcl, SWIG, CUDD and Eigen from GitHub too -- the recipe is in the handoff's tools/TOOLS.md;
# with NEO_YOSYS / NEO_STA / NEO_PDK_LIB set, synth.py never looks here.
set -e
T=${NEO_SYNTH_TOOLS:-/opt/neo-synth}
OSS_DATE=2026-10-11; OSS_TAG=20261011
ORFS_COMMIT=3451ca1f56902a49baf50bb8eba345eb709cf9c8
STA_COMMIT=f861c6278db5c68198701da9c08a328830751829
CUDD_TAG=cudd-3.0.0
JOBS=${JOBS:-$(nproc)}
SUDO=""; [ "$(id -u)" != 0 ] && SUDO=sudo
mkdir -p "$T"
cd "$T"
echo "== synth tools into $T ($(date -u)) =="

# ---- OSS CAD Suite (yosys) ----------------------------------------------------------------------------------------------
if [ ! -x "$T/oss-cad-suite/bin/yosys" ]; then
  echo "-- OSS CAD Suite $OSS_DATE"
  curl -sSL -o oss-cad-suite.tgz "https://github.com/YosysHQ/oss-cad-suite-build/releases/download/$OSS_DATE/oss-cad-suite-linux-x64-$OSS_TAG.tgz"
  tar xzf oss-cad-suite.tgz && rm -f oss-cad-suite.tgz
fi
"$T/oss-cad-suite/bin/yosys" -V
[ -f "$T/oss-cad-suite/share/yosys/plugins/slang.so" ] || { echo "slang plugin missing"; exit 1; }

# ---- the libraries from OpenROAD-flow-scripts ---------------------------------------------------------------------------
if [ ! -f "$T/pdk/lib/asap7/asap7sc7p5t_SEQ_RVT_SS_nldm_220123.lib" ]; then
  echo "-- OpenROAD-flow-scripts platforms at ${ORFS_COMMIT:0:7}"
  rm -rf orfs && mkdir orfs && cd orfs
  git init -q && git remote add origin https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts
  git sparse-checkout init --cone
  git sparse-checkout set flow/platforms/asap7/lib/NLDM flow/platforms/asap7/yoSys flow/platforms/nangate45/lib flow/platforms/common
  git fetch -q --depth 1 --filter=blob:none origin "$ORFS_COMMIT"
  git checkout -q FETCH_HEAD
  echo "   ORFS $(git rev-parse --short HEAD)"
  P=flow/platforms
  mkdir -p "$T/pdk/lib/asap7" "$T/pdk/lib/nangate45" "$T/pdk/lib/common"
  for c in SS TT; do
    for f in $P/asap7/lib/NLDM/asap7sc7p5t_{AO,OA,SIMPLE}_RVT_${c}_nldm_211120.lib.gz $P/asap7/lib/NLDM/asap7sc7p5t_INVBUF_RVT_${c}_nldm_220122.lib.gz; do
      gunzip -c "$f" > "$T/pdk/lib/asap7/$(basename "$f" .gz)"
    done
    cp $P/asap7/lib/NLDM/asap7sc7p5t_SEQ_RVT_${c}_nldm_220123.lib "$T/pdk/lib/asap7/"
  done
  cp $P/asap7/yoSys/cells_adders_R.v "$T/pdk/lib/asap7/cells_adders.v"
  cp $P/nangate45/lib/NangateOpenCellLibrary_typical.lib $P/nangate45/lib/fakeram45_2048x39.lib "$T/pdk/lib/nangate45/"
  cp $P/nangate45/cells_adders.v "$T/pdk/lib/nangate45/cells_adders.v" 2>/dev/null || cp flow/platforms/nangate45/cells_adders.v "$T/pdk/lib/nangate45/cells_adders.v"
  cp $P/common/lcu_kogge_stone.v "$T/pdk/lib/common/"
  cd "$T" && rm -rf orfs
fi
ls "$T/pdk/lib/asap7" | head -3

# ---- OpenSTA ------------------------------------------------------------------------------------------------------------
# the binary links libtcl8.6 dynamically: the runtime package every run (a cache hit skips the build), the build packages
# only when building
if command -v apt-get >/dev/null 2>&1; then
  $SUDO apt-get update -qq > /dev/null
  $SUDO apt-get install -y -qq libtcl8.6 > /dev/null
fi
if [ ! -x "$T/sta" ]; then
  echo "-- OpenSTA ${STA_COMMIT:0:7} (build from source)"
  if command -v apt-get >/dev/null 2>&1; then
    $SUDO apt-get install -y -qq cmake tcl-dev swig bison flex libeigen3-dev zlib1g-dev automake libtool > /dev/null
  fi
  rm -rf cudd && git clone -q --depth 1 --branch $CUDD_TAG https://github.com/ivmai/cudd cudd
  ( cd cudd && autoreconf -fi > /dev/null 2>&1 && ./configure --prefix="$T/cudd-install" > /dev/null && make -j"$JOBS" > /dev/null && make install > /dev/null )
  rm -rf opensta && mkdir opensta && cd opensta
  git init -q && git remote add origin https://github.com/parallaxsw/OpenSTA
  git fetch -q --depth 1 origin "$STA_COMMIT"
  git checkout -q FETCH_HEAD
  echo "   OpenSTA $(git rev-parse --short HEAD)"
  mkdir build && cd build
  cmake .. -DCMAKE_BUILD_TYPE=Release -DCUDD_DIR="$T/cudd-install" -DUSE_TCL_READLINE=OFF > cmake.log 2>&1
  make -j"$JOBS" > make.log 2>&1
  cp sta "$T/sta"
  cd "$T" && rm -rf opensta cudd
fi
"$T/sta" -version
ldd "$T/sta" | grep -E "tcl|not found" || true
echo "== synth tools ready ($(date -u)) =="
