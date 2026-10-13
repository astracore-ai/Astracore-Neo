// neo_iso.sv -- isolation cell, behavioural (drop 0.43, stream B2): the boundary of a tile's switchable power domain. While
//   iso_en is high the W outputs of the domain are clamped to CLAMP (0 or 1 on every bit) whatever the domain drives -- an
//   unpowered domain's outputs float -- and pass unchanged otherwise. The cells sit in the always-on side (neo_tile places
//   them between the tile and its router / the control plane); a power-aware flow maps them onto the library's isolation
//   cells (an AND for clamp-0, an OR for clamp-1, which is what the synthesis of this behavioural form gives).
module neo_iso #(
  parameter int W = 1,
  parameter int CLAMP = 0                    // the value of every bit while isolated: 0 or 1
)(
  input  logic         iso_en,
  input  logic [W-1:0] d,                    // from the switchable domain
  output logic [W-1:0] q                     // to the always-on side
);
  assign q = iso_en ? {W{CLAMP != 0}} : d;
endmodule
