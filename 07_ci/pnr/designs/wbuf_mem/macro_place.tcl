# macro_place.tcl -- wbuf_mem's one macro against the core's right edge, R0 (drop 0.43, stream A3): FakeRAM2.0 puts every
# signal pin on the macro's left edge (936 write and 936 read data bits, the addresses), so R0 at the right edge turns them
# to the core's interior, where the 12 SECDED lanes are. ORFS's macro placer put it at the left edge mirrored (MX), its pin
# edge 11 um from the die boundary: global routing failed with overflow on M6 (run 183). acc_bank's and act_feeder's
# macros, placed with their pin edges inward, routed clean.
set block [ord::get_db_block]
set dbu [$block getDbUnitsPerMicron]
set core [$block getCoreArea]
foreach inst [$block getInsts] {
  if { [[$inst getMaster] getType] == "BLOCK" } {
    set w [expr {[[$inst getMaster] getWidth] / double($dbu)}]
    set x [expr {[$core xMax] / double($dbu) - $w - 2.0}]
    set y [expr {[$core yMin] / double($dbu) + 2.0}]
    puts "macro_place.tcl: [$inst getName] ([[$inst getMaster] getName]) at $x $y R0"
    place_macro -macro_name [$inst getName] -location [list $x $y] -orientation R0
  }
}
