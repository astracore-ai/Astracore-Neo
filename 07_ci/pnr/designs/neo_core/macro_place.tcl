# macro_place.tcl -- neo_core's three arrays with their pin edges toward the core (drop 0.44, stream A4): FakeRAM2.0 puts
# every signal pin on the macro's left edge. Run 182's macro placer put the three on the bottom edge -- the weight buffer
# R180 (pins upward, fine), the accumulator R0 and the feeder's 331.8 um-tall macro MX at x = 657 um, the feeder's pin
# edge facing the accumulator's back across 10 um with half of its 1,048 pins (pnr_drop043.md section 4: the wbuf_mem /
# tile_nic overflow of runs 183 / 188 waiting to happen). Here: the weight buffer (121.4 x 166.6) and the accumulator
# (137.0 x 166.6) stacked against the left edge mirrored (MY: pins inward), the feeder (66.5 x 331.8) against the right
# edge R0 (pins inward), 4 um apart (twice the platform's 2 um halo); rtl_macro_placer then finds every macro placed.
set block [ord::get_db_block]
set dbu [$block getDbUnitsPerMicron]
set core [$block getCoreArea]
set x0 [expr {[$core xMin] / double($dbu)}]
set y0 [expr {[$core yMin] / double($dbu)}]
set x1 [expr {[$core xMax] / double($dbu)}]
set yl [expr {$y0 + 2.0}]
set yr [expr {$y0 + 2.0}]
foreach inst [$block getInsts] {
  set m [$inst getMaster]
  if { [$m getType] != "BLOCK" } { continue }
  set n [$inst getName]
  set w [expr {[$m getWidth] / double($dbu)}]
  set h [expr {[$m getHeight] / double($dbu)}]
  if { [string match "*feeder*" $n] } {
    puts "macro_place.tcl: $n ([$m getName]) at [expr {$x1 - $w - 2.0}] $yr R0 (the feeder, right edge, pins inward)"
    place_macro -macro_name $n -location [list [expr {$x1 - $w - 2.0}] $yr] -orientation R0
    set yr [expr {$yr + $h + 4.0}]
  } else {
    puts "macro_place.tcl: $n ([$m getName]) at [expr {$x0 + 2.0}] $yl MY (left edge, pins inward)"
    place_macro -macro_name $n -location [list [expr {$x0 + 2.0}] $yl] -orientation MY
    set yl [expr {$yl + $h + 4.0}]
  }
}
