# macro_place.tcl -- bank_bist_wrap's 16 lane macros in rows (drop 0.43, stream A3): as many macros per row as the core's
# width holds with a channel of NEO_CHANNEL um (20) in front of each, rows stacked bottom-up 4 um apart (twice the
# platform's 2 um macro halo), in the lanes' order; every macro R0, so its signal pins -- all on FakeRAM2.0's left edge
# -- face the channel to its left, where the lane's encoder and decoder go. rtl_macro_placer then finds every macro
# placed.
set block [ord::get_db_block]
set dbu [$block getDbUnitsPerMicron]
set core [$block getCoreArea]
set x0 [expr {[$core xMin] / double($dbu)}]
set y0 [expr {[$core yMin] / double($dbu)}]
set cw [expr {[$core dx] / double($dbu)}]
set ch 20.0
set macros {}
foreach inst [$block getInsts] {
  if { [[$inst getMaster] getType] == "BLOCK" } {
    set n [$inst getName]
    set k 0
    regexp {u_lane([0-9]+)} $n -> k
    lappend macros [list $k $inst]
  }
}
set macros [lsort -integer -index 0 $macros]
set m0 [[lindex [lindex $macros 0] 1] getMaster]
set w [expr {[$m0 getWidth] / double($dbu)}]
set h [expr {[$m0 getHeight] / double($dbu)}]
set per_row [expr {max(1, int(($cw - 2.0) / ($w + $ch)))}]
set i 0
foreach entry $macros {
  set inst [lindex $entry 1]
  set col [expr {$i % $per_row}]
  set row [expr {$i / $per_row}]
  set x [expr {$x0 + $ch + $col * ($w + $ch)}]
  set y [expr {$y0 + 2.0 + $row * ($h + 4.0)}]
  puts "macro_place.tcl: [$inst getName] ([[$inst getMaster] getName]) at $x $y R0 (row $row, column $col)"
  place_macro -macro_name [$inst getName] -location [list $x $y] -orientation R0
  incr i
}
