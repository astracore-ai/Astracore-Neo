// neo_icg.sv -- integrated clock-gating cell, behavioural (drop 0.43, stream B2): the one clock gate of the design. Every
//   architecturally gated block of the tile takes its clock from one of these (the core's datapath, the two requantizers,
//   the NIC's receive and transmit halves, the router's five output stages, the two DMA engines), and one more gates the
//   tile's switchable power domain (neo_tile's clk_t, enabled by tile_clk_en from the control plane's power sequencer) in
//   front of every block but the router's stages -- those gates are chained behind it.
//   Mechanism: the latch-based ICG of every standard-cell library -- a latch transparent while clk is low holds en | te,
//   and gclk = clk & latched enable. The enable is sampled during the low phase and frozen through the high phase, so a
//   change of en after the rising edge (it comes from flops of that edge) cannot cut or create a pulse: no glitch, and a
//   gated edge is a whole clock pulse coincident with clk's (zero-delay simulation: one delta behind it, before any
//   nonblocking update of that edge is visible, so a gated flop samples what an ungated one does). te forces the clock
//   on (the scan shift's test enable; tied 0 in the RTL, the DFT flow connects it).
//   The enable's timing: it must settle before the rising edge it gates (a full-cycle path from the flops that form it;
//   the latch is the cell's own, its timing the library's ICG arcs).
//   Simulators (every one must agree, and does):
//   - Verilator (5.020, --timing as in every cocotb build) and Icarus (12 and 14) simulate this latch as written;
//   - the synthesis flow (07_ci/synth/synth.py) black-boxes neo_icg in every unit and maps the instances onto the
//     library's ICG cell (ASAP7 ICGx1_ASAP7_75t_R, Nangate45 CLKGATETST_X1), counted in the report with the clock-gated
//     flop fraction per unit;
//   - neosim (02_dv/sim/neosim.py) has no latch: it takes neo_icg as a primitive -- en | te sampled at the settle before
//     each rising edge (the latch's value at the edge), and an always_ff clocked by gclk evaluated at that edge only when
//     the sample is 1 (or its asynchronous reset is asserted). That is the AND of clk with the enable registered on the
//     falling edge whenever the enable is stable through the low phase -- which it is in neosim, where the testbench
//     changes inputs between two edges;
//   - the formal harnesses (02_dv/formal, SymbiYosys without multiclock, which takes no derived clock) read the formal
//     model neo_icg_formal.sv instead -- gclk = clk, ungated -- and assert that the enable is high in every cycle in which
//     a gated register would change, i.e. that gating it would be invisible.
module neo_icg (
  input  logic clk,
  input  logic en,          // the block's clock enable: high in every cycle whose rising edge the block needs
  input  logic te,          // test enable: the clock on regardless (scan shift)
  output logic gclk
);
  logic en_l;               // the enable latched while clk is low
  always_latch begin
    if (!clk) en_l = en | te;
  end
  assign gclk = clk & en_l;
endmodule
