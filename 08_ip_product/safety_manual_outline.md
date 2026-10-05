# Neo NPU IP — Safety Manual outline (SEooC, ISO 26262-10 Clause 9)

What the assessor and the licensee's safety manager expect to find, section by section. Items marked [pkg]
already exist in the package; the rest is phase-1 work.

1. Scope and assumptions of use — the block as a Safety Element out of Context; assumed item: perception for an
   ASIL-D ADAS function with a diverse monitor; assumed FTTI (detect ≤ 10 ms, react ≤ 20 ms); environment (Grade 1
   or 2), clock/reset/power assumptions, host responsibilities. [pkg: safety concept draft, FMEDA Assumptions sheet]
2. Safety requirements allocated to the block — the technical safety requirements the block fulfils (detect any
   single-point fault in compute, storage and transfer within the FTTI; signal through the error pin and cause
   registers; never deliver a corrupted result as valid without a flag). [pkg: R7–R10 and the mechanism table]
3. Safety mechanisms — ABFT check column; ABFT at drain over reductions; duplicated feeder control; lockstep
   sequencer; duplicated requantization with table parity; weight-buffer ECC; bank SECDED and MBIST; link parity;
   end-to-end CRC with word count; fetch timeout; reduction flow control; ESM with watchdog; checker self-test.
   For each: fault model covered, detection latency, coverage claim, the test that proves it. [pkg: FMEDA v1 Evidence]
4. Hardware metrics — SPFM, LFM, PMHF per tile and per configuration, base failure rates and SER sources, with
   the fault-simulation campaign results replacing the directed-test claims at RTL freeze. [pkg: FMEDA v1, claims]
5. Integration requirements for the licensee — what the host must do: handle the error pin within the FTTI, run the
   MBIST and the checker self-test on the island's schedule, kick the watchdog, protect the register bus, supply
   ECC-capable memories if the bank is not used, provide the diverse monitor. Register timing rules. [pkg: host_if header]
6. Verification and validation of the block — regression suites, fault-injection suites, coverage, formal
   properties, silicon validation on the test chip. [pkg: logs, dv/]
7. Tool confidence — compiler and runtime classified under ISO 26262-8 Clause 11; TCL reduced by verified output
   (every compiled network checked bit-exact against the reference model); qualification evidence for the
   remaining tool chain. [pkg: the bit-exact flow; the argument is phase-1]
8. Known limitations and anomalies — LBIST not included (licensee's DFT); sparsity and non-INT8 modes in release 2;
   the diverse monitor is outside the block.
9. Configuration management and change control — versioned deliverables, errata process, impact analysis of
   configuration parameters (tile count, bank size, link width) on the metrics.
10. Certificate — the assessor's confirmation of the SEooC safety case at ASIL-B(D) HW metrics and ASIL-D process.
