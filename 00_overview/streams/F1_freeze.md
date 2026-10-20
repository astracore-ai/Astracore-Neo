# Stream F1 -- the freeze (drop 0.70): criteria, the integration specification, the traceability matrix, the tagged
# release, HANDOFF-6

Branch `f1-freeze`, after 0.44 (S1, A4, D3) is merged. Read COMMON.md, `HANDOFF-5.md`, `00_overview/ROADMAP.md` §1 (the
Freeze row), the spec, the drop log, `08_ip_product/` (the datasheet, the safety-manual outline, the test-chip plan, the
integration note), `05_safety/fmeda_v3.md`, `02_dv/formal/README.md`, `09_synthesis/proxy_to_target.md`.

## The freeze criteria (each a line of the release note with its proof)

1. Every CI job green on the release commit: python-regress, hdl-sim, hdl-mesh8, synth (both libraries, the sweep's verdict
   at 1,250 ps for the tile and the chip top, the gate-level simulations), formal, a `faults` dispatch at the nightly size,
   a `pnr` dispatch of the default blocks; the Icarus steps gating; the standing warnings zero.
2. FMEDA v3 at or under the 100 FIT ASIL-B(D) chip PMHF with measured coverage, or the spec's §8 restated to the measured
   figure with the residual named.
3. The spec's claims ledger (§0) with no row left at "planned" that the RTL does not back, every "model" row naming its
   proxy figure and what a foundry library replaces; the datasheet (`08_ip_product/neo_npu_ip_datasheet.md`) aligned with
   the spec row by row.
4. The integration specification (`08_ip_product/neo_integration_spec.md`, from `neo_top_integration.md`): clocks and
   resets (domains, the three crossing structures, the reset order, the power sequencer's states), every interface's
   protocol (AXI4 master / slave widths, IDs, outstanding limits; AXI4-Lite map; `irq` / `err`; the straps), the memories
   (every leaf instance with its organisation, ports, the macro the compiler replaces it with, the ECC around it), the IP
   boundaries (island, PHYs, controllers, LBIST / DFT hooks), the clock-gating and isolation cells to map, the constraints
   (`09_synthesis/constraints.sdc` regenerated from `synth.py`'s templates for the tile and the top), the test hooks to
   remove or fuse (`DV_HOOKS`, `fault_inject`).
5. The requirement -> mechanism -> test / proof -> coverage traceability matrix, generated (`05_safety/traceability.py`) from
   the test docstrings (every cocotb / neosim / SV test names its mechanism), the formal README's table, the FMEDA rows and
   the coverage report: one CSV + a Markdown view; a requirement without a test, or a test without a requirement, is a
   finding listed at the top.
6. The release: a tag `v0.70-rc1`, a `RELEASE.md` (what the package is, how to build and run everything from a clean machine
   -- the CI's steps are the recipe --, the tool versions pinned, the known limitations), HANDOFF-6 (the state for the
   implementation team: what is RTL-proven, what is proxy-measured, what is model, the open items with owners), the drop
   log's 0.70 row, the Doc's section.

## Verification and report

The release commit's runs as the criteria list; the matrix's findings resolved or listed; the spec and datasheet diffed row
by row. Report: the criteria table with each proof's run number, the matrix's counts, the tag, anything deferred past the
freeze with its owner.
