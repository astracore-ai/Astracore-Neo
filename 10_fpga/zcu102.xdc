# zcu102.xdc -- the tile runs on the PS-provided pl_clk0 (100 MHz default; raise to 120 MHz in the PS config)
set_property PACKAGE_PIN AG14 [get_ports err_pin_led]
set_property IOSTANDARD LVCMOS33 [get_ports err_pin_led]
