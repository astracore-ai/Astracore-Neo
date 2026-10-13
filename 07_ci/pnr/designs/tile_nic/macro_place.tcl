# macro_place.tcl -- tile_nic's two macros stacked against the core's left edge, mirrored (MY), their pin edges inward
# (drop 0.43, stream A3): FakeRAM2.0 puts every signal pin on the macro's left edge (the partial-sum rows' 2,093 pins on
# 100.8 um, the drain FIFO's 2,157 on 166.6 um), so MY at the left edge turns them to the core, where the NIC's logic is
# -- the placement run 174 routed clean with (the macro placer's choice then). In run 188 the macro placer put the rows'
# macro MX at the left edge, its pin edge 11 um from the die boundary, and global routing overflowed on M6. A macro that
# does not fit in the column goes against the right edge, R0, its pins inward as well.
set block [ord::get_db_block]
set dbu [$block getDbUnitsPerMicron]
set core [$block getCoreArea]
set x0 [expr {[$core xMin] / double($dbu)}]
set y0 [expr {[$core yMin] / double($dbu)}]
set x1 [expr {[$core xMax] / double($dbu)}]
set y1 [expr {[$core yMax] / double($dbu)}]
set macros {}
foreach inst [$block getInsts] {
  if { [[$inst getMaster] getType] == "BLOCK" } {
    lappend macros [list [expr {[[$inst getMaster] getHeight] / double($dbu)}] $inst]
  }
}
set y [expr {$y0 + 2.0}]
set yr [expr {$y0 + 2.0}]
foreach entry [lsort -real -index 0 $macros] {
  set h [lindex $entry 0]
  set inst [lindex $entry 1]
  set w [expr {[[$inst getMaster] getWidth] / double($dbu)}]
  if { $y + $h + 2.0 <= $y1 } {
    puts "macro_place.tcl: [$inst getName] ([[$inst getMaster] getName]) at [expr {$x0 + 2.0}] $y MY"
    place_macro -macro_name [$inst getName] -location [list [expr {$x0 + 2.0}] $y] -orientation MY
    set y [expr {$y + $h + 4.0}]
  } else {
    puts "macro_place.tcl: [$inst getName] ([[$inst getMaster] getName]) at [expr {$x1 - $w - 2.0}] $yr R0"
    place_macro -macro_name [$inst getName] -location [list [expr {$x1 - $w - 2.0}] $yr] -orientation R0
    set yr [expr {$yr + $h + 4.0}]
  }
}
