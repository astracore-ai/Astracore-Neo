// neo_core_pkg.sv -- UVM environment skeleton for neo_core (drop 0.11).
//   Reference model: the Python golden model and compiler (sim/run_conv_core.py --dump) write the
//   buffers, the descriptor and the expected rows as hex; the sequence item points at that set.
//   What is complete: item, driver protocol (buffers -> descriptor -> go -> collect until done),
//   monitor, scoreboard against the expected rows and the error flags, coverage of the descriptor
//   space and the safety flags. What the DV team adds: constrained-random convolution shapes with
//   the golden model invoked through DPI, the K-split sequences (two ext-port agents), and the
//   tile-level environment on top of tile_mesh.
package neo_core_pkg;
  import uvm_pkg::*;
  `include "uvm_macros.svh"

  class neo_layer_item extends uvm_sequence_item;
    `uvm_object_utils(neo_layer_item)
    string vecdir;                 // directory with abuf.hex, wbuf.hex, y_exp.hex, desc.vh values
    rand int unsigned h, w, cin, cout, k, s, p;
    rand int unsigned ct0, rn, contrib_n;
    bit inject_mac_fault, inject_ctrl_fault, inject_seq_fault, inject_rq_fault;
    constraint c_shape { k inside {1, 3}; s inside {1, 2}; p == k / 2; h inside {[4:16]}; w inside {[4:16]};
                         cin inside {[1:64]}; cout inside {[1:32]}; }
    function new(string name = "neo_layer_item"); super.new(name); endfunction
  endclass

  class neo_driver extends uvm_driver #(neo_layer_item);
    `uvm_component_utils(neo_driver)
    virtual neo_core_if vif;
    function new(string name, uvm_component parent); super.new(name, parent); endfunction
    function void build_phase(uvm_phase phase);
      if (!uvm_config_db#(virtual neo_core_if)::get(this, "", "vif", vif)) `uvm_fatal("NOVIF", "neo_core_if not set")
    endfunction
    task run_phase(uvm_phase phase);
      neo_layer_item it;
      forever begin
        seq_item_port.get_next_item(it);
        vif.load_buffers(it.vecdir);        // $readmemh abuf/wbuf into the DMA write ports, one entry per clock
        vif.write_descriptor(it);
        vif.pulse_go();
        vif.wait_done();
        seq_item_port.item_done();
      end
    endtask
  endclass

  class neo_row_txn extends uvm_sequence_item;
    `uvm_object_utils(neo_row_txn)
    int row;
    longint data[];
    longint chk;
    bit abft_err;
    function new(string name = "neo_row_txn"); super.new(name); endfunction
  endclass

  class neo_monitor extends uvm_monitor;
    `uvm_component_utils(neo_monitor)
    virtual neo_core_if vif;
    uvm_analysis_port #(neo_row_txn) ap;
    function new(string name, uvm_component parent); super.new(name, parent); ap = new("ap", this); endfunction
    function void build_phase(uvm_phase phase);
      if (!uvm_config_db#(virtual neo_core_if)::get(this, "", "vif", vif)) `uvm_fatal("NOVIF", "neo_core_if not set")
    endfunction
    task run_phase(uvm_phase phase);
      int row = 0;
      forever begin
        @(negedge vif.clk);
        if (vif.rd_valid) begin
          neo_row_txn t = neo_row_txn::type_id::create("t");
          t.row = row++; t.data = new[vif.COLS];
          foreach (t.data[j]) t.data[j] = vif.rd_data[j];
          t.chk = vif.rd_chk; t.abft_err = vif.acc_abft_err;
          ap.write(t);
        end
        if (vif.done) row = 0;
      end
    endtask
  endclass

  class neo_scoreboard extends uvm_scoreboard;
    `uvm_component_utils(neo_scoreboard)
    uvm_analysis_imp #(neo_row_txn, neo_scoreboard) imp;
    longint expected[][];          // loaded from y_exp.hex by the test
    int mismatches = 0, rows = 0;
    function new(string name, uvm_component parent); super.new(name, parent); imp = new("imp", this); endfunction
    function void write(neo_row_txn t);
      rows++;
      foreach (t.data[j]) if (t.row < expected.size() && t.data[j] !== expected[t.row][j]) begin
        mismatches++;
        `uvm_error("SB", $sformatf("row %0d col %0d: got %0d expected %0d", t.row, j, t.data[j], expected[t.row][j]))
      end
      if (t.abft_err) `uvm_error("SB", $sformatf("ABFT flagged row %0d without an injected fault", t.row))
    endfunction
    function void report_phase(uvm_phase phase);
      `uvm_info("SB", $sformatf("%0d rows, %0d mismatches", rows, mismatches), UVM_LOW)
    endfunction
  endclass

  class neo_coverage extends uvm_subscriber #(neo_layer_item);
    `uvm_component_utils(neo_coverage)
    neo_layer_item it;
    covergroup cg;
      k: coverpoint it.k { bins k1 = {1}; bins k3 = {3}; }
      s: coverpoint it.s { bins s1 = {1}; bins s2 = {2}; }
      cin: coverpoint it.cin { bins one_tile = {[1:32]}; bins many = {[33:64]}; }
      role: coverpoint it.contrib_n { bins solo = {0}; bins owner = {[1:8]}; }
      faults: coverpoint {it.inject_mac_fault, it.inject_ctrl_fault, it.inject_seq_fault, it.inject_rq_fault};
      cross k, s;
    endgroup
    function new(string name, uvm_component parent); super.new(name, parent); cg = new(); endfunction
    function void write(neo_layer_item t); it = t; cg.sample(); endfunction
  endclass

  class neo_agent extends uvm_agent;
    `uvm_component_utils(neo_agent)
    neo_driver drv; neo_monitor mon; uvm_sequencer #(neo_layer_item) sqr;
    function new(string name, uvm_component parent); super.new(name, parent); endfunction
    function void build_phase(uvm_phase phase);
      drv = neo_driver::type_id::create("drv", this); mon = neo_monitor::type_id::create("mon", this);
      sqr = uvm_sequencer #(neo_layer_item)::type_id::create("sqr", this);
    endfunction
    function void connect_phase(uvm_phase phase); drv.seq_item_port.connect(sqr.seq_item_export); endfunction
  endclass

  class neo_env extends uvm_env;
    `uvm_component_utils(neo_env)
    neo_agent agt; neo_scoreboard sb; neo_coverage cov;
    function new(string name, uvm_component parent); super.new(name, parent); endfunction
    function void build_phase(uvm_phase phase);
      agt = neo_agent::type_id::create("agt", this); sb = neo_scoreboard::type_id::create("sb", this);
      cov = neo_coverage::type_id::create("cov", this);
    endfunction
    function void connect_phase(uvm_phase phase); agt.mon.ap.connect(sb.imp); endfunction
  endclass

  class neo_c1_seq extends uvm_sequence #(neo_layer_item);
    `uvm_object_utils(neo_c1_seq)
    function new(string name = "neo_c1_seq"); super.new(name); endfunction
    task body();
      neo_layer_item it = neo_layer_item::type_id::create("it");
      start_item(it);
      it.vecdir = "vectors_core"; it.h = 6; it.w = 6; it.cin = 40; it.cout = 7; it.k = 3; it.s = 1; it.p = 1;
      it.ct0 = 0; it.rn = 27; it.contrib_n = 0;
      finish_item(it);
    endtask
  endclass

  class neo_base_test extends uvm_test;
    `uvm_component_utils(neo_base_test)
    neo_env env;
    function new(string name, uvm_component parent); super.new(name, parent); endfunction
    function void build_phase(uvm_phase phase); env = neo_env::type_id::create("env", this); endfunction
    task run_phase(uvm_phase phase);
      neo_c1_seq seq = neo_c1_seq::type_id::create("seq");
      phase.raise_objection(this);
      seq.start(env.agt.sqr);
      #100ns;
      phase.drop_objection(this);
    endtask
  endclass
endpackage
