# wbuf_mem (drop 0.44, A4): the read data's output budget as its consumer needs it. rdata / rcdata go to the array's
# shadow chain -- mac_pe's w_in into the w_sh register under w_load, one mux level (systolic_array.sv) -- not to a
# unit's 0.2-of-the-period input logic; the routed block (run 188) missed 1,250 ps by 90 ps on 376 of these outputs
# with the generic 0.2 (the macro's read, the 12 SECDED decoders and the wire to the pins against 250 ps left). The
# budget here is 0.1 of the period: the register's setup, its mux and the wire of a neighbouring unit; prep.py appends
# this file to the block's SDC with {P} the period in the library's unit (ps on ASAP7).
set_output_delay -clock clk -max [expr {P} * 0.1] [get_ports {rdata* rcdata*}]
