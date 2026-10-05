# Program decisions at handoff

| # | Decision | Default in force | Alternative | Needed by | Status |
| --- | --- | --- | --- | --- | --- |
| 1 | Accelerator or SoC | accelerator + ASIL-D safety island, PCIe Gen5 host-attached | full SoC | — | closed 2026-10-04 |
| 2 | Compute point | 65,536 MACs at 2.0 GHz worst-case (262 TOPS dense) | 24,576 MACs; two-die first | — | closed 2026-10-04 |
| 3 | Process and foundry | TSMC N5A | N3A; Samsung SF5A | — | closed 2026-10-04 |
| 4 | Safety target | ASIL-D systematic; island ASIL-D; accelerator ASIL-B(D); item ASIL-D by decomposition | full-chip ASIL-D HW metrics | 2026-12-15 | open |
| 5 | DRAM bus | 384-bit LPDDR5X-8533 (410 GB/s) | 256-bit at 9600 (307 GB/s); 512-bit at 6400 | 2026-12-15 | open |
| 6 | Cockpit scope | no display/GPU/audio in gen 1; DMS and LLM assistant kept | display pipeline + small GPU | 2026-12-15 | open |
| 7 | Program size | ~290 people at peak, $160–260 M to production silicon | smaller team / longer schedule / 98-TOPS variant | 2026-12-15 | open |
| 8 | Lead OEM or Tier-1 | sign a development partner before phase 0 exits; item definition, HARA, safety goals and FTTI from them | generic safety concept | 2027-01-15 | open |
| 9 | Chiplet timing | single die in gen 1; UCIe ports designed in for gen 1.5 | two-die in gen 1 | 2027-01-15 | open |

Gates from the product strategy: letter of intent at architecture freeze; development agreement and a customer-network
FPGA demo at spec freeze; Neo-L reuse plan at spec freeze. No customer at spec freeze means re-scope, not proceed.
