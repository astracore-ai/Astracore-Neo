# zcu102.tcl -- Vivado project for one neo_tile_axil on ZCU102 (skeleton, drop 0.16)
create_project neo_zcu102 ./neo_zcu102 -part xczu9eg-ffvb1156-2-e
set rtl [glob ../rtl/*.sv]
add_files -norecurse $rtl fpga_top.sv
add_files -fileset constrs_1 zcu102.xdc
set_property top fpga_top [current_fileset]
create_bd_design "design_1"
create_bd_cell -type ip -vlnv xilinx.com:ip:zynq_ultra_ps_e:3.5 zynq_ultra_ps_e_0
apply_bd_automation -rule xilinx.com:bd_rule:zynq_ultra_ps_e -config {apply_board_preset "1"} [get_bd_cells zynq_ultra_ps_e_0]
set_property -dict [list CONFIG.PSU__USE__M_AXI_GP2 {1} CONFIG.PSU__MAXIGP2__DATA_WIDTH {32}] [get_bd_cells zynq_ultra_ps_e_0]
create_bd_cell -type module -reference fpga_top fpga_top_0
apply_bd_automation -rule xilinx.com:bd_rule:axi4 -config {Master "/zynq_ultra_ps_e_0/M_AXI_HPM0_LPD" Clk "Auto"} [get_bd_intf_pins fpga_top_0/s_axi]
assign_bd_address
validate_bd_design
make_wrapper -files [get_files design_1.bd] -top
add_files -norecurse [get_files design_1_wrapper.v]
set_property top design_1_wrapper [current_fileset]
launch_runs impl_1 -to_step write_bitstream -jobs 8
