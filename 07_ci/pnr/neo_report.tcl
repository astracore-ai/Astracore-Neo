# neo_report.tcl -- the routed block's own report (drop 0.42, stream A2b). run.py runs it through ORFS after the final
# stage (`make run RUN_SCRIPT=<this> RUN_LOG_NAME_STEM=7_neo_report`, so ORFS's load_design reads the platform's
# libraries, LEFs and RC) on 6_final.odb / 6_final.sdc with the final stage's OpenRCX parasitics (6_final.spef); after a
# stage failed or ran out of time, on the last stage's database (NEO_REPORT_ODB / NEO_REPORT_SDC) with placement
# estimates. Writes $REPORTS_DIR/neo/*.txt for report.py: the slacks, the register-to-register minimum period (the
# figure synth.py reports per unit) beside the one with the I/O paths, the worst paths, the clock skew and the cells
# driving clock nets, the power at the flow's default activity, die / core / cell areas, wire length.
source $::env(SCRIPTS_DIR)/load.tcl
set odb [expr { [info exists ::env(NEO_REPORT_ODB)] ? $::env(NEO_REPORT_ODB) : "6_final.odb" }]
set sdc [expr { [info exists ::env(NEO_REPORT_SDC)] ? $::env(NEO_REPORT_SDC) : "6_final.sdc" }]
load_design $odb $sdc
set out $::env(REPORTS_DIR)/neo
file mkdir $out

set routed [expr { $odb == "6_final.odb" || $odb == "5_route.odb" || $odb == "5_3_fillcell.odb" }]
set spef $::env(RESULTS_DIR)/6_final.spef
if { $odb == "6_final.odb" && [file exists $spef] } {
  read_spef $spef
  set par "rcx 6_final.spef"
} else {
  estimate_parasitics -placement
  set par "placement estimate"
}
if { $odb != "2_floorplan.odb" && $odb != "3_place.odb" } {
  set_propagated_clock [all_clocks]
}

# one report command with its `> file` / `>> file` redirection (OpenSTA's report commands take it, as
# scripts/report_metrics.tcl uses them); a command that fails is logged and the report goes on
proc rpt { args } {
  if { [catch { uplevel 1 $args } msg] } {
    puts "neo_report: [lindex $args 0] failed: $msg"
  }
}

proc value { script } {
  if { [catch { set v [uplevel 1 $script] } msg] } {
    return "NA"
  }
  return $v
}

set f [open $out/summary.txt w]
puts $f "odb $odb"
puts $f "sdc $sdc"
puts $f "parasitics $par"
puts $f "wns_max [value {worst_slack -max}]"
puts $f "tns_max [value {total_negative_slack -max}]"
puts $f "wns_min [value {worst_slack -min}]"
puts $f "tns_min [value {total_negative_slack -min}]"
puts $f "setup_violations [value {sta::endpoint_violation_count max}]"
puts $f "hold_violations [value {sta::endpoint_violation_count min}]"
puts $f "max_slew_violations [value {sta::max_slew_violation_count}]"
puts $f "max_cap_violations [value {sta::max_capacitance_violation_count}]"
puts $f "max_fanout_violations [value {sta::max_fanout_violation_count}]"
puts $f "time_unit [value {sta::unit_scale_suffix time}]"
puts $f "power_unit [value {sta::unit_scale_suffix power}]"
close $f

rpt report_clock_min_period > $out/min_period.txt
rpt report_clock_min_period -include_port_paths > $out/min_period_ports.txt
rpt report_checks -path_delay max -group_path_count 10 -endpoint_path_count 1 -unique_paths_to_endpoint \
  -fields {slew cap fanout} -digits 1 -format full_clock_expanded > $out/paths_max.txt
rpt report_checks -path_delay max -from [all_registers] -to [all_registers] -group_path_count 5 \
  -endpoint_path_count 1 -fields {slew cap fanout} -digits 1 -format full_clock_expanded > $out/paths_reg2reg.txt
rpt report_checks -path_delay min -group_path_count 5 -digits 1 -format full_clock_expanded > $out/paths_min.txt
rpt report_clock_skew -setup -digits 1 > $out/skew.txt
rpt report_clock_skew -hold -digits 1 >> $out/skew.txt
rpt report_power > $out/power.txt
rpt report_check_types -max_slew -max_capacitance -max_fanout -violators > $out/drv.txt

# the setup-violating endpoints (all of them, worst last is not guaranteed: report.py sorts) and the count of all
set f [open $out/endpoints.txt w]
set n_end 0
foreach pin [sta::endpoints] {
  incr n_end
  set s [value {get_property $pin slack_max}]
  if { [string is double -strict $s] && $s < 0 } {
    puts $f "[get_full_name $pin] $s"
  }
}
close $f
set f [open $out/summary.txt a]
puts $f "endpoints $n_end"
close $f

# the physical side from the database: areas, the cells by kind, the cells that drive clock nets, wire length
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
  } elseif { [$m isFiller] || [string match FILLER* $name] || [string match DECAP* $name] } {
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
set wl 0
set wired 0
foreach net [$block getNets] {
  set w [$net getWire]
  if { $w != "NULL" } {
    if { ![catch { set l [$w getLength] }] } {
      set wl [expr { $wl + $l }]
      incr wired
    }
  }
}
set f [open $out/physical.txt w]
puts $f "die_um [expr { [$die dx] / $dbu }] [expr { [$die dy] / $dbu }]"
puts $f "core_um [expr { [$core dx] / $dbu }] [expr { [$core dy] / $dbu }]"
puts $f "die_area_um2 [expr { [$die dx] * [$die dy] / ($dbu * $dbu) }]"
puts $f "core_area_um2 [expr { [$core dx] * [$core dy] / ($dbu * $dbu) }]"
dict for {k v} $kinds {
  puts $f "${k}_count [lindex $v 0]"
  puts $f "${k}_area_um2 [lindex $v 1]"
}
puts $f "clock_net_drivers $clk_cells"
puts $f "clock_net_driver_masters [dict get $clk_masters]"
puts $f "nets [llength [$block getNets]]"
puts $f "wired_nets $wired"
puts $f "wire_length_um [expr { $wl / $dbu }]"
puts $f "routed $routed"
close $f
puts "neo_report: $out ($odb, $par)"
