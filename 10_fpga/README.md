# FPGA proof-of-concept flow (skeleton, drop 0.16; untested here: no Vivado in the development sandbox)

Target: AMD ZCU102 (Zynq UltraScale+ XCZU9EG). One `neo_tile_axil` at the simulated configuration (ROWS = 16,
COLS = 8, 4,096-word bank) with the mesh links tied off, the AXI-Lite port on the PS's M_AXI_HPM0_LPD, the
error pin on a PL LED and the PS GPIO. The host driver (`sw/neo_host.c`) runs on the A53 under Linux with the
register window mapped through UIO; `sw/test_backend.c`'s program is the first thing to run, then the M1 flow
from `sim/gen_tile_vectors.py` (bank image written through a debug port, see below).

Files: `zcu102.tcl` (Vivado 2025.x project and block design), `zcu102.xdc` (clock and LED), `fpga_top.sv`
(wrapper tying off links; adds a bank backdoor over AXI-Lite at 0x400 for loading images).

Expected: ~120 MHz on the UltraScale+ fabric for the 16x8 tile (the 2-stage PE and the registered router
outputs are the critical paths at this size); resource estimate 70k LUT, 40 BRAM36 (bank and buffers), 136 DSP48
(MAC array with check column). A 2-tile mesh fits; the 32x32 silicon tile needs a VCK190 or an emulator.

Status: skeleton to be completed on a machine with Vivado; the first bitstream is a phase-1 deliverable.
