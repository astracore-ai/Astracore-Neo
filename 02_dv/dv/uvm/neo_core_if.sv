// neo_core_if.sv -- interface between the UVM agent and neo_core's ports (drop 0.11 skeleton).
interface neo_core_if #(parameter int ROWS = 16, COLS = 8, PW = 32) (input logic clk);
  import neo_core_pkg::*;
  logic rst_n, go, done, rd_valid, acc_abft_err;
  logic signed [PW-1:0] rd_data [COLS];
  logic signed [PW-1:0] rd_chk;
  // DMA write ports, descriptor and flags are driven by the tasks below (bodies: see tb/tb_neo_core.sv, which
  // the DV team lifts into these tasks; the skeleton keeps the protocol in one place)
  task automatic load_buffers(string vecdir); endtask
  task automatic write_descriptor(neo_layer_item it); endtask
  task automatic pulse_go(); @(negedge clk); go = 1; @(negedge clk); go = 0; endtask
  task automatic wait_done(); wait (done); @(negedge clk); endtask
endinterface
