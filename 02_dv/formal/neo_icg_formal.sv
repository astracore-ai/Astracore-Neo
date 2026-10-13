// neo_icg_formal.sv -- the formal model of rtl/neo_icg.sv (drop 0.43): the harnesses read this file instead of the RTL cell.
//   SymbiYosys without multiclock takes no derived clock (a flop clocked by clk & latch is refused: "derived clocks are
//   only supported with clk2fflogic"), so the gate is transparent here -- gclk = clk, every gated register updates by its
//   own logic every cycle -- and each harness asserts instead that the enable is high in every cycle in which one of the
//   gated registers changes: gating the clock in the other cycles changes nothing, which is what the latch then does (its
//   own behaviour -- no glitch, the enable sampled while clk is low -- is the library cell's and the simulators', not a
//   property of the design).
module neo_icg (
  input  logic clk,
  input  logic en,
  input  logic te,
  output logic gclk
);
  assign gclk = clk;
  logic unused_en;
  assign unused_en = en | te;
endmodule
