# Gate-level simulation of synthesized units against the RTL (`make gls`, the synth job)

Each unit's netlist (ASAP7 cells, the PDK's behavioural models, Icarus 14 in zero delay) beside its RTL at the same parameters, the same random inputs each cycle, every output compared each cycle from cycle 8 (rst_n low in cycles 0-3). A mismatch is a known bit that differs; an X-only compare is a netlist X where the RTL is known.

| Unit | Module | Cells | Cycles compared | Mismatches | X-only compares (cycles) | Result |
| --- | --- | ---: | ---: | ---: | --- | --- |
| mac_pe | mac_pe | 1253 | 2992 | 0 | 0 | PASS |
| mac_pe_chk | mac_pe | 1694 | 2992 | 0 | 0 | PASS |
| requant | requant | 111546 | 2992 | 0 | 0 | PASS |
| crc16_beat | crc16_beat | 3430 | 2992 | 0 | 0 | PASS |
| ecc39_enc | ecc39_enc | 66 | 2992 | 0 | 0 | PASS |
| ecc39_dec | ecc39_dec | 163 | 2992 | 0 | 0 | PASS |
| link_packer | link_packer | 24029 | 2992 | 0 | 0 | PASS |
