# neo_report.tcl -- what ORFS's final report leaves out (drop 0.42, stream A2b). run.py runs it through ORFS after the
# final stage (`make run RUN_SCRIPT=<this> RUN_LOG_NAME_STEM=7_neo_report`, so ORFS's load_design reads the platform's
# libraries, LEFs and RC) on 6_final.odb / 6_final.sdc; after a stage failed or ran out of time, on the last stage's
# database (NEO_REPORT_ODB / NEO_REPORT_SDC). ORFS's 6_finish.rpt and metrics carry the slacks, the minimum period,
# skew, power and the cell counts; this adds, in $REPORTS_DIR/neo/: physical.txt (die / core, the cells by kind and
# area, the cells driving clock nets by master, wire length -- from the database alone, written first), then with the
# timing set up as ORFS's final report does (the propagated clock, then the final stage's OpenRCX SPEF; placement
# estimates for an earlier database): paths_max.txt (the ten worst endpoints, one path each) and endpoints.txt (every
# setup-violating endpoint with its slack). Every step is announced in the log before it runs; run.py does not fail the
# job on this script.
source $::env(SCRIPTS_DIR)/load.tcl
set odb [expr { [info exists ::env(NEO_REPORT_ODB)] ? $::env(NEO_REPORT_ODB) : "6_final.odb" }]
set sdc [expr { [info exists ::env(NEO_REPORT_SDC)] ? $::env(NEO_REPORT_SDC) : "6_final.sdc" }]
load_design $odb $sdc
set out $::env(REPORTS_DIR)/neo
file mkdir $out

proc step { msg } {
  puts "neo_report: $msg"
  flush stdout
}

step "physical (database)"
set block [ord::get_db_block]
set dbu [expr { double([$block getDbUnitsPerMicron]) }]
set die [$block getDieArea]
set core [$block getCoreArea]
set kinds [dict create stdcell {0 0.0} macro {0 0.0} tap {0 0.0} filler {0 0.0} tie {0 0.0}]
set clk_cells 0
set clk_masters [dict create]
foreach inst [$block getInsts] {
  set m [$inst getMaster]
  set name [$m getName]
  set a [expr { [$m getWidth] * [$m getHeight] / ($dbu * $dbu) }]
  if { [$m getType] == "BLOCK" } {
    set k macro
  } elseif { [string match TAPCELL* $name] } {
    set k tap
  } elseif { [string match FILLER* $name] || [string match DECAP* $name] } {
    set k filler
  } elseif { [string match TIE* $name] } {
    set k tie
  } else {
    set k stdcell
  }
  lassign [dict get $kinds $k] n s
  dict set kinds $k [list [expr { $n + 1 }] [expr { $s + $a }]]
  foreach it [$inst getITerms] {
    if { [$it isOutputSignal] } {
      set net [$it getNet]
      if { $net != "NULL" && [$net getSigType] == "CLOCK" } {
        incr clk_cells
        dict incr clk_masters $name
        break
      }
    }
  }
}
set f [open $out/physical.txt w]
puts $f "odb $odb"
puts $f "die_um [expr { [$die dx] / $dbu }] [expr { [$die dy] / $dbu }]"
puts $f "core_um [expr { [$core dx] / $dbu }] [expr { [$core dy] / $dbu }]"
dict for {k v} $kinds {
  puts $f "${k}_count [lindex $v 0]"
  puts $f "${k}_area_um2 [lindex $v 1]"
}
puts $f "clock_net_drivers $clk_cells"
puts $f "clock_net_driver_masters $clk_masters"
foreach inst [$block getInsts] {
  if { [[$inst getMaster] getType] == "BLOCK" } {
    set bb [$inst getBBox]
    puts $f "macro [$inst getName] [[$inst getMaster] getName] [expr { [$bb xMin] / $dbu }] [expr { [$bb yMin] / $dbu }]\
      [$inst getOrient]"
  }
}
close $f

step "timing: the propagated clock"
set_propagated_clock [all_clocks]
set spef $::env(RESULTS_DIR)/6_final.spef
if { $odb == "6_final.odb" && [file exists $spef] } {
  step "timing: read_spef $spef"
  read_spef $spef
} else {
  step "timing: estimate_parasitics -placement"
  estimate_parasitics -placement
}
step "timing: the ten worst endpoints"
report_checks -path_delay max -group_path_count 10 -endpoint_path_count 1 -unique_paths_to_endpoint \
  -fields {slew cap fanout} -digits 1 -format full_clock_expanded > $out/paths_max.txt
step "timing: the violating endpoints"
set f [open $out/endpoints.txt w]
foreach pin [sta::endpoints] {
  set s [get_property $pin slack_max]
  if { [string is double -strict $s] && $s < 0 } {
    puts $f "[get_full_name $pin] $s"
  }
}
close $f
step "done: $out"
