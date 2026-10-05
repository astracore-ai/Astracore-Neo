# Verification ramp kit (drop 0.11)

What is here, and what it saves the DV team:

| Item | File | Use |
| --- | --- | --- |
| Assertion modules for every protocol in the design | `sva/neo_sva.sv`, `sva/bind_all.sv` | Compile with the RTL under Xcelium (`-sv +define+ASSERT`) or load into JasperGold as the property set for formal; every property is a rule the architecture documents already state |
| UVM skeleton for `neo_core` | `uvm/neo_core_pkg.sv`, `uvm/neo_core_if.sv` | Item, driver protocol, monitor, scoreboard against golden rows, covergroups; the Python golden model and compiler (`sim/run_conv_core.py --dump`) produce the vectors |
| Directed testbenches for Xcelium / Verilator / Icarus | `tb/tb_neo_mac_core.sv`, `tb/tb_neo_core.sv` | Replay of the neosim regressions on a sign-off simulator (`make xrun`, `make xrun_core`) |
| Python harnesses as executable specs | `sim/run_*.py` | Every test the RTL has passed, with the stimulus and checks spelled out |

Regression plan the team inherits:
1. Day 1: `make regress` (Python + neosim) and the CI workflow (`.github/workflows/regress.yml`), which installs Verilator and Icarus and runs the directed testbenches on every push. Any disagreement between a sign-off simulator and neosim is a neosim bug until proven otherwise.
2. Week 1: bind `sva/bind_all.sv` into the Xcelium runs; triage every assertion firing.
3. Month 1: UVM environment live with constrained-random convolutions (golden model via DPI); K-split sequences with two reduce-port agents.
4. Month 2: JasperGold on the router (deadlock freedom with the RDY protocol, parity never emitted bad), the sequencer FSM, the DMA engine, and the ABFT invariant on the streaming core.
5. Coverage targets at RTL freeze: functional ≥ 95 %, code ≥ 99 %, every FMEDA mechanism exercised by a fault-injection test.
