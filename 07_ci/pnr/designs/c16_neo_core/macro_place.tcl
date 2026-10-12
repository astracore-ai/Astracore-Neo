# macro_place.tcl -- the fallback macro placement of c16_neo_core (drop 0.42, stream A2b), used only with
# NEO_MACRO_PLACEMENT=1 (config.mk) when ORFS's macro placer fails: the macros stacked bottom-up on the core's left
# edge, largest first, mirrored about the y axis (MY) so that FakeRAM's signal pins -- all on the macro's left edge --
# face the core, 4 um apart (twice the platform's 2 um macro halo); rtl_macro_placer then finds every macro placed.
set block [ord::get_db_block]
set dbu [$block getDbUnitsPerMicron]
set core [$block getCoreArea]
set x [expr {[$core xMin] / double($dbu) + 2.0}]
set y [expr {[$core yMin] / double($dbu) + 2.0}]
set macros {}
foreach inst [$block getInsts] {
  set m [$inst getMaster]
  if { [$m getType] == "BLOCK" } {
    lappend macros [list [expr {[$m getWidth] * [$m getHeight]}] $inst]
  }
}
foreach entry [lsort -decreasing -real -index 0 $macros] {
  set inst [lindex $entry 1]
  set m [$inst getMaster]
  puts "macro_place.tcl: [$inst getName] ([$m getName]) at $x $y MY"
  place_macro -macro_name [$inst getName] -location [list $x $y] -orientation MY
  set y [expr {$y + [$m getHeight] / double($dbu) + 4.0}]
}
