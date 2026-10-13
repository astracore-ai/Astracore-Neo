// skew_in.sv -- delays activation row i (and the swap token) by i*PE_LAT cycles so that partial
// sums line up in a weight-stationary array whose PEs have a PE_LAT-cycle psum path
// (row 0 is a pass-through).
//   Drop 0.42 -- the row buses of the sparse-capable array (NB = 4; NB = 1 is the skew of drop 0.41 bit for bit, the
//   row now a packed vector: byte c at [c*XW +: XW]). At NB = 4 x_vec is 2*ROWS bytes and each row's bus is four
//   bytes. Sparse mode (sparse = 1): byte c of x_vec is channel c of a 64-channel tile and rows 2g and 2g + 1 both
//   receive group g, channels 4g .. 4g + 3, each with its own skew. Dense mode: byte r is channel r (r < ROWS; the upper
//   bytes are unused) and row r's bus carries channel r in byte 0 and zeros in bytes 1..3, with one exception: row 0
//   has no register to map in, so its bus is bytes 0..3 as presented (channels 0..3) -- no PE of row 0 multiplies bytes
//   1..3 in dense mode (dense indices are 0, the check weights of columns 1..3 are 0).
//   The two rows of a pair share one delay chain of four bytes: the chain's first register takes the pair's group in
//   sparse mode and {0, 0, channel 2g + 1, channel 2g} in dense mode; row 2g takes the chain at delay 2g*PE_LAT through a
//   register of its own that keeps byte 0 alone in dense mode, row 2g + 1 at the chain's end, (2g + 1)*PE_LAT, through a
//   last register that moves byte 1 to byte 0 in dense mode. Per pair (2g + 1)*PE_LAT + 1 registers of 4*XW bits
//   instead of two chains (row 1: PE_LAT), 527 x 32 = 16,864 flops at ROWS = 32 against 31,744 for separate four-byte
//   lines and 7,936 for the one-byte lines of drop 0.41; the swap token keeps its one-bit line per row. PE_LAT >= 2.
//   The row is packed (it was an unpacked array of bytes): Verilator 5.020 does not re-evaluate logic computed from the
//   elements of an unpacked array a testbench writes (the four-byte buses of row 0 and of the chains' inputs held their
//   reset values in tb_neo_mac_core, the settle-only scheduling of drop 0.40's lesson); from a packed vector it does.
module skew_in #(
  parameter int ROWS   = 32,
  parameter int XW     = 8,
  parameter int PE_LAT = 2,
  parameter int NB     = 1,                          // bytes per row bus: 1 (dense-only), 4 (2:4 sparse-capable, drop 0.42)
  parameter int XB     = (NB > 1) ? 2 * ROWS : ROWS  // bytes presented per cycle (derived)
)(
  input  logic                 clk,
  input  logic                 rst_n,
  input  logic [XB*XW-1:0]     x_vec,          // unskewed: one activation row in one cycle, byte c at [c*XW +: XW]
  input  logic                 sparse,         // NB = 4: the layer is 2:4 sparse (a descriptor bit, static during a layer)
  input  logic                 s_in,           // swap token, one cycle before row 0 of a tile
  output logic [NB*XW-1:0]     x_row [ROWS],   // skewed row buses: row i delayed by i*PE_LAT cycles
  output logic                 s_row [ROWS]
);
  // the swap token: one bit per row
  generate
    for (genvar i = 0; i < ROWS; i++) begin : g_srow
      if (i == 0) begin : g_s0
        assign s_row[i] = s_in;
      end else begin : g_sn
        logic signed [0:0] sq;
        delay_line #(.W(1), .DEPTH(i*PE_LAT)) u_ds (.clk(clk), .rst_n(rst_n), .d(s_in), .q(sq));
        assign s_row[i] = sq[0];
      end
    end
  endgenerate

  generate
    if (NB == 1) begin : g_dense
      for (genvar i = 0; i < ROWS; i++) begin : g_row
        if (i == 0) begin : g_r0
          assign x_row[i] = x_vec[0 +: XW];
        end else begin : g_rn
          delay_line #(.W(XW), .DEPTH(i*PE_LAT)) u_dl (.clk(clk), .rst_n(rst_n), .d(x_vec[i*XW +: XW]), .q(x_row[i]));
        end
      end
      logic unused_sparse;
      assign unused_sparse = sparse;
    end else begin : g_pairs
      localparam int GW = 4 * XW;                   // a group: four bytes
      assign x_row[0] = x_vec[0 +: GW];             // row 0 as presented
      for (genvar g = 0; g < ROWS / 2; g++) begin : g_pair
        logic [GW-1:0] st1;                         // the chain's first register: the pair's group (sparse) or its two channels (dense)
        logic [GW-1:0] tap_b;                       // the chain at delay (2g + 1)*PE_LAT - 1
        logic [GW-1:0] r_odd;                       // row 2g + 1's last register
        always_ff @(posedge clk or negedge rst_n) begin
          if (!rst_n)      st1 <= '0;
          else if (sparse) st1 <= x_vec[4*g*XW +: GW];
          else             st1 <= {{(2*XW){1'b0}}, x_vec[2*g*XW +: 2*XW]};
        end
        if (g == 0 && PE_LAT == 2) begin : g_first
          // row 1 at delay PE_LAT: the first register, then row 1's own
          assign tap_b = st1;
        end else if (g == 0) begin : g_first_long
          delay_line #(.W(GW), .DEPTH(PE_LAT-2)) u_db (.clk(clk), .rst_n(rst_n), .d(st1), .q(tap_b));
        end else begin : g_next
          logic [GW-1:0] tap_a;                     // the chain at delay 2g*PE_LAT - 1
          logic [GW-1:0] r_even;                    // row 2g's own register
          delay_line #(.W(GW), .DEPTH(2*g*PE_LAT-2)) u_da (.clk(clk), .rst_n(rst_n), .d(st1), .q(tap_a));
          delay_line #(.W(GW), .DEPTH(PE_LAT))       u_db (.clk(clk), .rst_n(rst_n), .d(tap_a), .q(tap_b));
          always_ff @(posedge clk or negedge rst_n) begin
            if (!rst_n) r_even <= '0;
            else        r_even <= sparse ? tap_a : {{(3*XW){1'b0}}, tap_a[XW-1:0]};
          end
          assign x_row[2*g] = r_even;
        end
        always_ff @(posedge clk or negedge rst_n) begin
          if (!rst_n) r_odd <= '0;
          else        r_odd <= sparse ? tap_b : {{(3*XW){1'b0}}, tap_b[2*XW-1:XW]};
        end
        assign x_row[2*g+1] = r_odd;
      end
    end
  endgenerate
endmodule
