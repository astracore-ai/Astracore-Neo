# Concept-review pack for the functional-safety assessor (drop 0.11)

Why now: an assessor's concept review in month 2 costs days; the same findings at RTL freeze cost months. The
FMEDA has already changed the architecture three times (R7, R10 and the activation-buffer CRC); the assessor's
view of the decomposition and the assumptions of use should arrive before the sequencer and the tile interface
freeze.

## What to send
1. Safety concept draft (doc section "Safety concept draft") with the assumptions of use and the decomposition:
   item-level ASIL-D = Neo accelerator ASIL-B(D) + independent diverse monitor ASIL-B(D); safety island ASIL-D.
2. FMEDA skeleton (`safety/neo_fmeda_skeleton.xlsx`): Assumptions, FMEDA_core, Metrics, Chip_rollup, with every
   coverage value marked as an engineering claim and the evidence pointer where one exists (test name).
3. Mechanism evidence table: the fault-injection tests that exist today and what each proves.

| Mechanism | Covers | Evidence (test, drop) |
| --- | --- | --- |
| ABFT check column per core | any single-point fault in a MAC, a stored weight or a psum chain, within the same output row | T2–T4 (model), T2 (RTL 32×32), C4, K2 |
| ABFT at drain | accumulator memory and the reduction transfer | C4 (bit flip in acc memory), K2 (corrupted partial-sum row in flight) |
| Duplicated feeder control (R7) | wrong address / wrong sequence, invisible to ABFT | C5 |
| Lockstep sequencer (R10) | wrong run order / drain index, invisible to ABFT | C7 |
| Duplicated requantization (R10) | wrong INT8 output downstream of ABFT | C8 |
| Link parity | single flit corruption on a link | N2 (NoC), M3 shows its limit |
| End-to-end CRC per message | corruption between source bank and destination buffer, including NIC logic | M3 |
| (39,32) SECDED on banks | single-bit errors corrected, double detected | ecc39 exhaustive; M5 in flight |
| Flow control (RDY) | reduction deadlock | M2 (found and fixed) |

4. Open items the assessor should see as open: weight-buffer ECC, requant table parity, periodic self-test of
   the checkers (latent-fault coverage), LBIST/MBIST scheduling within the FTTI, timeouts and sequence numbers on
   the mesh, the item definition and FTTI from the lead OEM.

## Questions to put to the assessor
- Is the decomposition (ASIL-B(D) accelerator + diverse monitor) acceptable for the intended perception item, and
  what independence evidence will be required between the two?
- Which base failure rates and SER sources are acceptable for N5A in the FMEDA (ISO 26262-11, SN 29500, foundry data)?
- Is ABFT accepted as a safety mechanism for the datapath with the coverage argument given (single-point faults per
  row), and what fault-simulation evidence will be required at RTL freeze?
- For the checkers' own latent faults, is periodic fault injection through the DV hooks acceptable as the in-field
  self-test, and at what interval relative to the FTTI?
