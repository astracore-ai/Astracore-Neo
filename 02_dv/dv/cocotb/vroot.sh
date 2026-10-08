#!/bin/sh
# vroot.sh (drop 0.37) -- print a VERILATOR_ROOT that holds the programs AND the include tree, for hierarchical verilation.
#
# --hierarchical verilates every hier block in a child run: the generated Vtop_hier.mk launches the `verilator` wrapper
# again, and by then VERILATOR_ROOT is in the environment (verilator_bin exports its compiled-in default). Distribution
# packages split the install -- Ubuntu's verilator 5.020 puts verilator_bin in /usr/bin and include/ with verilator_includer
# under /usr/share/verilator, the compiled-in root -- so the relaunched wrapper looks for $VERILATOR_ROOT/bin/verilator_bin,
# finds nothing, and the block's verilation dies with "exec: /usr/share/verilator/verilator_bin: not found" (run 126).
# A source build installed under one prefix, or a kit directory, is complete and needs nothing.
#
# Prints: $VERILATOR_ROOT when it is complete; else the compiled-in root (`verilator --getenv VERILATOR_ROOT`) when that
# is; else a directory of symbolic links -- bin/ with every verilator program, include/ -- built next to this script
# (sim_build_vroot, or $VROOT_DIR). Prints nothing when no verilator is on the PATH (the build then reports that itself).
# The Makefile exports the result for the hierarchical builds; a flat build never consults it.
here=$(cd "$(dirname "$0")" && pwd)
farm=${VROOT_DIR:-$here/sim_build_vroot}
complete() { [ -x "$1/bin/verilator_bin" ] && [ -f "$1/include/verilated.mk" ] && [ -e "$1/bin/verilator_includer" ]; }
if [ -n "$VERILATOR_ROOT" ]; then
  if complete "$VERILATOR_ROOT"; then echo "$VERILATOR_ROOT"; exit 0; fi
  root=$VERILATOR_ROOT
else
  root=$(verilator --getenv VERILATOR_ROOT 2>/dev/null || true)
fi
[ -n "$root" ] || exit 0
if complete "$root"; then echo "$root"; exit 0; fi
mkdir -p "$farm/bin" || exit 0
[ -d "$root/include" ] && ln -sfn "$root/include" "$farm/include"
for f in "$root"/bin/*; do [ -e "$f" ] && ln -sfn "$f" "$farm/bin/$(basename "$f")"; done
for p in verilator verilator_bin verilator_bin_dbg verilator_includer verilator_ccache_report verilator_coverage; do
  [ -e "$farm/bin/$p" ] && continue
  q=$(command -v "$p" 2>/dev/null || true)
  [ -n "$q" ] && ln -sfn "$q" "$farm/bin/$p"
done
complete "$farm" && echo "$farm"
exit 0
