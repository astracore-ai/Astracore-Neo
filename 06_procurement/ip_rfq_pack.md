# AstraCore Neo — IP procurement pack (RFQ-ready), drop 0.11

Purpose: start licensing on day one. Every item below is on the 6–12-month lead-time path and none depends
on further architecture work. Process: TSMC N5A. Qualification: AEC-Q100 Grade 2 (Ta −40 to +105 °C), Tj −40 to
+125 °C, 15-year mission profile. Safety: the item is developed to ISO 26262; PHY and core IP must ship a safety
manual and FMEDA data sufficient for an ASIL-B(D) element (the safety island's core for ASIL-D).

Common deliverables to request from every vendor: hard macro or synthesizable RTL as noted; GDS/LEF/LIB at the
automotive corners (SS/FF/TT, −40/125 °C) with aging derating; UPF and power-state tables; verification IP and
compliance test suites; timing and SI models (IBIS-AMI for SerDes); safety manual, FMEDA/base failure rates per
ISO 26262-11; test-chip or production silicon report in N5A; errata process; integration support hours.

| # | IP | Requirement (from spec v2.1) | Form | Lead time | Candidates (to be confirmed by procurement) | Decision criteria |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | LPDDR5X PHY + controller | 384-bit total (6 × 64-bit channels), up to 8533 MT/s, inline ECC, link ECC, ASIL-B safety package, N5A hard macro | hard PHY + soft controller | 9–12 mo | Cadence, Synopsys | N5A silicon proof, ECC coverage data, controller QoS features for 64 tile requesters |
| 2 | PCIe Gen5 PHY + controller | ×8, endpoint and root complex, SR-IOV, AER, N5A | hard PHY + soft controller | 6–9 mo | Cadence, Synopsys | compliance status, host-side driver maturity, power at ×8 |
| 3 | MIPI D-PHY v2.5 / C-PHY + CSI-2 controllers | 4 × 4-lane ≥ 4.5 Gbps/lane, 16 virtual channels per port, frame CRC/E2E hooks | hard PHY + soft controller | 6 mo | Cadence, Synopsys, Arasan | automotive GMSL/FPD-Link deserializer interop reports |
| 4 | 10G Ethernet SerDes + MAC/TSN/MACsec | 2 × 10G (USXGMII) + 4 × 1G/2.5G (SGMII), 802.1AS-2020, Qbv, Qbu, CB; MACsec | hard SerDes + soft MAC | 6 mo | Cadence, Synopsys, Comcores (MAC/TSN) | 802.3ch automotive PHY interop, TSN conformance |
| 5 | UCIe 1.1 PHY (gen 1.5 only) | 2 × ×16 modules, standard package, 32 GT/s | hard | 9–12 mo | Cadence, Synopsys | organic-substrate (standard package) support, automotive grade |
| 6 | Cortex-R52+ (safety island) | dual-core lockstep pair, ASIL-D package, Arm safety manual, ECC on TCM | soft | 6–9 mo to license | Arm | license terms for lockstep, availability of the safety case |
| 7 | HSM / crypto | EVITA Full: AES-GCM, SHA-2/3, ECDSA P-256/384, Ed25519, ML-KEM-768, ML-DSA-65, LMS; TRNG SP 800-90B; glitch/DPA hardening | soft + hard TRNG | 6 mo | Rambus, Synopsys, Secure-IC | PQC readiness (FIPS 203/204), ISO 21434 evidence, side-channel reports |
| 8 | Vector DSP (radar/lidar front end) | ConnX-class, FFT/CFAR/DoA, sparse-conv support | soft | 3–6 mo | Cadence Tensilica | automotive toolchain qualification (ISO 26262-8) |
| 9 | ISP + H.265 encoder | ≥ 2.5 Gpix/s, 12 streams, 3-exposure HDR, LED flicker mitigation; 4 × 1080p30 H.265 | soft | 6–9 mo | Arm Mali-C class, Allegro DVT, VeriSilicon | HDR pipeline quality on automotive sensors, safety package for the ISP |
| 10 | N5A memory compilers + std-cell libraries | single-port SRAM for the 64 × 2 MB banks (ECC-ready word organisation), register files for the per-core buffers, HVT/ULVT mix for Grade 2 leakage | compiled macros | 3 mo | TSMC, Synopsys, Arm | leakage at 125 °C, SER data (FIT/Mbit) for the FMEDA, MBIST/repair support |
| 11 | PLLs, monitors, eFuse/OTP | per-domain PLLs, ≥ 16 thermal sensors, voltage monitors, OTP for keys and anti-rollback | hard | 6 mo | foundry ecosystem (TSMC-qualified vendors) | automotive qualification data |

Procurement sequence that keeps IP off the critical path: issue RFQs for 1, 2, 6 and 10 in week 1 (longest and
most architecture-defining); 3, 4, 7, 9, 11 by week 4; 5 and 8 by month 3. Evaluation copies (encrypted RTL, LIB/LEF)
are enough to start integration and trial synthesis in phase 1; production GDS follows the licence.
