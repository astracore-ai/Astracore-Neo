# Neo test chip (2027) — plan

**Purpose.** Silicon proof for the IP: measured TOPS/W, maximum frequency at the automotive corners, SRAM
yield with ECC and MBIST, and the safety mechanisms exercised with real faults (voltage/temperature margining,
radiation test of the SER assumptions). A licensee's architect does not sign on models.

**What goes on it.** Two tiles (the full tile: core, 2 MB bank, interface, DMA engine, host registers, ESM, MBIST)
on a 2×1 mesh; a minimal host: JTAG/SPI register bridge, a small RISC-V or Cortex-M controller to run the DMA
programs and the self-test schedule, clock generation with external reference, test pads for the error pin and
the watchdog; no PHYs, no DRAM (activations and weights preloaded into the banks through the bridge).

**Process and vehicle.** TSMC N5A (or N5 with automotive libraries if N5A shuttle slots are unavailable) on a
multi-project-wafer run; die ~4 mm²; ~40 bare dies plus a few hundred in a cheap package; budget $0.5–1.0 M
including masks share, packaging, test board and a radiation test campaign.

**Schedule.** RTL freeze of the test-chip configuration month 6 after funding; physical design months 6–10 using
the reference flow (this is also the dry run of the implementation kit); tape-out month 10–11; silicon month
14–15; characterization months 15–18, in time for the first licence negotiations.

**Measurements that become datasheet numbers.** Fmax vs voltage at −40/25/125/150 °C; energy per MAC at the
two operating points; leakage vs temperature; SRAM VMIN and ECC correction rates; detection latency of each safety
mechanism under injected faults; MBIST coverage on actual defects; neutron/alpha SER of the bank (replacing the
600 FIT/Mbit assumption that dominates the chip PMHF today).

**Exit criteria.** Measured efficiency within 30 % of the model at both points; every safety mechanism detected
its injected fault class within the FTTI; MBIST and ECC behaviour as specified; a characterization report that
goes into the datasheet and the safety manual.
