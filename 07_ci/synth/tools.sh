#!/bin/bash
# tools.sh -- the synthesis job's tools (drop 0.40; fix38 made the OpenSTA build diagnosable and non-fatal), installed into
# $NEO_SYNTH_TOOLS (default /opt/neo-synth; the CI job sets /home/runner/neo-synth):
#   oss-cad-suite/   YosysHQ OSS CAD Suite nightly $OSS_DATE (yosys with the slang SystemVerilog front end, yosys-abc;
#                    GitHub release tarball, no apt)
#   sta              OpenSTA (parallaxsw, $STA_COMMIT) built from source -- cmake, tcl-dev, swig, bison, flex, libfl-dev, libeigen3-dev
#                    from apt on the runner (OpenSTA's own Dockerfile.ubuntu24.04 recipe), CUDD 3.0.0 from GitHub; the
#                    binary is what actions/cache keeps. If the build fails, the script prints the cmake / make logs, leaves
#                    no $T/sta and exits 0: synth.py then runs the synthesis and its gates without timing and fails the
#                    timing gate, so the job is red for the right reason with the synthesis report published.
#   pdk/lib/asap7    ASAP7 7.5-track RVT NLDM liberty files (SS, TT: AO, INVBUF, OA, SIMPLE, SEQ), gunzipped, and the
#                    platform's full-adder map; pdk/lib/nangate45: NangateOpenCellLibrary_typical.lib and its adder map;
#                    pdk/lib/common/lcu_kogge_stone.v -- all from OpenROAD-flow-scripts $ORFS_COMMIT (sparse checkout)
# Every version is pinned here; every download is a GitHub clone or release asset. Idempotent: a tool that is already
# there is kept (CI caches $NEO_SYNTH_TOOLS/sta between runs; the rest downloads in about a minute).
# The sandbox (no apt) builds Tcl, SWIG, CUDD and Eigen from GitHub too -- the recipe is in the handoff's tools/TOOLS.md;
# with NEO_YOSYS / NEO_STA / NEO_PDK_LIB set, synth.py never looks here. STA_CMAKE_EXTRA adds cmake arguments to the
# OpenSTA build (the sandbox passes its Tcl / flex / Eigen locations that way); NEO_SYNTH_NO_APT=1 skips apt (the sandbox).
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
# NEO_TOOLS_ONLY=oss (the formal job, drop 0.41): the pinned OSS CAD Suite alone -- sby, yosys with the slang plugin,
# yosys-smtbmc, the solvers, yosys-abc -- and nothing after it (no ORFS libraries, no OpenSTA build, no cache)
if [ "${NEO_TOOLS_ONLY:-}" = oss ]; then
  echo "== NEO_TOOLS_ONLY=oss: the OSS CAD Suite only, $T/oss-cad-suite/bin ($(date -u)) =="
  exit 0
fi

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
# only when building. The build is a function run without `set -e` abort: its failure is reported, not fatal.
APT=""; command -v apt-get >/dev/null 2>&1 && [ -z "$NEO_SYNTH_NO_APT" ] && APT=1      # NEO_SYNTH_NO_APT=1: the sandbox
if [ -n "$APT" ]; then
  $SUDO apt-get update -qq > /dev/null 2>&1 || echo "   (apt-get update failed; going on)"
  $SUDO apt-get install -y -qq libtcl8.6 tcl8.6 > /dev/null 2>&1 || echo "   (apt: the Tcl runtime packages did not install)"
fi
build_opensta() (
  set -e
  if [ -n "$APT" ]; then
    # libfl-dev: Debian ships FlexLexer.h there, not in flex -- OpenSTA's liberty scanner includes it (run 151's build error)
    $SUDO apt-get install -y -qq build-essential cmake tcl-dev tcl8.6 swig bison flex libfl-dev libeigen3-dev libfmt-dev \
      zlib1g-dev automake autoconf libtool > apt.log 2>&1 || { echo "   apt-get install of the build packages failed:"; tail -n 20 apt.log; exit 1; }
  fi
  # OpenSTA's build runs etc/TclEncode.tcl through `tclsh` on PATH: Debian ships tclsh8.6, the plain name only through
  # the alternatives (the sandbox has neither) -- a link in $T/bin covers both
  mkdir -p "$T/bin"
  if ! command -v tclsh >/dev/null 2>&1; then
    TCLSH86=$(command -v tclsh8.6 || true)
    [ -n "$TCLSH86" ] && ln -sf "$TCLSH86" "$T/bin/tclsh"
  fi
  export PATH="$T/bin:$PATH"
  echo "   tclsh: $(command -v tclsh || echo none) ; swig: $(swig -version 2>/dev/null | grep -i version | head -1) ; cmake: $(cmake --version | head -1) ; $(c++ --version | head -1)"
  cd "$T"
  rm -rf cudd && git -c advice.detachedHead=false clone -q --depth 1 --branch $CUDD_TAG https://github.com/ivmai/cudd cudd 2>&1 | grep -v "is not a commit" || true
  ( cd cudd && autoreconf -fi > /dev/null 2>&1 && ./configure --prefix="$T/cudd-install" > /dev/null 2>&1 && make -j"$JOBS" > /dev/null 2>&1 && make install > /dev/null 2>&1 )
  [ -f "$T/cudd-install/lib/libcudd.a" ] || { echo "   CUDD did not build"; exit 1; }
  echo "   CUDD $CUDD_TAG built"
  rm -rf opensta && mkdir opensta && cd opensta
  git init -q && git remote add origin https://github.com/parallaxsw/OpenSTA
  git fetch -q --depth 1 origin "$STA_COMMIT"
  git checkout -q FETCH_HEAD 2> /dev/null
  echo "   OpenSTA $(git rev-parse --short HEAD)"
  # hints for cmake where Debian's packages put things (empty elsewhere; STA_CMAKE_EXTRA for other layouts)
  HINTS=""
  [ -f /usr/include/tcl8.6/tcl.h ] && HINTS="$HINTS -DTCL_INCLUDE_PATH=/usr/include/tcl8.6"
  TCLLIB=$(ls /usr/lib/*/libtcl8.6.so /usr/lib64/libtcl8.6.so /usr/lib/libtcl8.6.so 2>/dev/null | head -1)
  [ -n "$TCLLIB" ] && HINTS="$HINTS -DTCL_LIBRARY=$TCLLIB"
  TCLSH=$(command -v tclsh8.6 || command -v tclsh || true)
  [ -n "$TCLSH" ] && HINTS="$HINTS -DTCL_TCLSH=$TCLSH"
  [ -f /usr/include/FlexLexer.h ] && HINTS="$HINTS -DFLEX_INCLUDE_DIR=/usr/include"
  [ -d /usr/share/eigen3/cmake ] && HINTS="$HINTS -DEigen3_DIR=/usr/share/eigen3/cmake"
  mkdir build && cd build
  echo "   cmake .. -DCMAKE_BUILD_TYPE=Release -DCUDD_DIR=$T/cudd-install -DUSE_TCL_READLINE=OFF $HINTS $STA_CMAKE_EXTRA"
  # shellcheck disable=SC2086
  cmake .. -DCMAKE_BUILD_TYPE=Release -DCUDD_DIR="$T/cudd-install" -DUSE_TCL_READLINE=OFF $HINTS $STA_CMAKE_EXTRA 2>&1 | tee cmake.log | grep -E "^-- (TCL|CUDD|SWIG|std::format|Found (FLEX|BISON|SWIG)|Configuring|Generating)|Error|error|NOTFOUND" || true
  [ -f Makefile ] || { echo "   cmake failed:"; tail -n 40 cmake.log; exit 1; }
  if ! make -j"$JOBS" > make.log 2>&1; then
    echo "   make failed:"; grep -n -i -m 20 "error" make.log; echo "   ..."; tail -n 40 make.log; exit 1
  fi
  BIN=$(find . -maxdepth 3 -type f -name sta -perm -u+x | head -1)
  [ -n "$BIN" ] || { echo "   no sta binary in the build tree"; exit 1; }
  cp "$BIN" "$T/sta"
  cd "$T" && rm -rf opensta cudd
)
if [ ! -x "$T/sta" ]; then
  echo "-- OpenSTA ${STA_COMMIT:0:7} (build from source)"
  if build_opensta; then
    echo "   OpenSTA built"
  else
    rm -f "$T/sta"
    echo "!! OpenSTA did not build -- the synth job runs the synthesis and its gates without timing and fails the timing gate"
  fi
fi
if [ -x "$T/sta" ]; then
  "$T/sta" -version
  ldd "$T/sta" | grep -E "tcl|not found" || true
fi
echo "== synth tools ready ($(date -u)) =="
