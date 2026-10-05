#!/usr/bin/env python3
"""
AstraCore Neo -- first-order architecture feasibility model (v0.1, 2026-10-04)

Purpose: check every headline number in Spec Rev 1.3 against arithmetic, and
produce the internally consistent numbers for the v2.0 baseline.

Everything is first-order and every assumption is a named constant below.
Change the constants, re-run, and the report regenerates. Workload sizes are
approximate public figures (1 MAC = 2 ops, the same convention as "TOPS").
"""
from dataclasses import dataclass
import sys

# ----------------------------------------------------------------------------
# Technology / system assumptions (N5-class automotive silicon)
# ----------------------------------------------------------------------------
E_MAC_PJ_AT_VREF = 0.45   # system-level energy per INT8 MAC at V_REF: datapath + local SRAM + NoC share
V_REF            = 0.75   # V, the voltage at which E_MAC_PJ_AT_VREF applies (~2.0 GHz worst-case)
UNCORE_W         = 11.0   # W: DRAM PHY + devices at high BW, PCIe5, 10GbE, ISP, safety island, clocks
LEAK_W_25C       = 2.0    # W: die leakage at 25 C for ~200 mm2 with HVT/ULVT mix
LEAK_DOUBLE_C    = 35.0   # leakage doubles every N degrees C
DRAM_EFF         = 0.75   # achievable fraction of raw DRAM bandwidth
TJ_MAX_C         = 125.0  # junction maximum
SPARSE_2_4       = 2.0    # throughput gain quoted by the industry for 2:4 structured sparsity


def vdd_for_freq(f_ghz: float) -> float:
    """Crude f-V curve for an N5-class MAC datapath at the slow corner."""
    return V_REF + 0.15 * (f_ghz - 2.0)


@dataclass
class Config:
    name: str
    n_mac: int
    f_ghz: float          # worst-case automotive-corner clock
    sram_mb: float
    dram_bits: int
    dram_mtps: float
    dies: int = 1

    @property
    def vdd(self): return vdd_for_freq(self.f_ghz)
    def tops_dense(self, bits=8): return self.dies * self.n_mac * 2 * self.f_ghz * (8 / bits) / 1e3
    def dram_gbps(self): return self.dies * self.dram_bits / 8 * self.dram_mtps / 1e3
    def dram_eff_gbps(self): return self.dram_gbps() * DRAM_EFF
    def e_mac_pj(self): return E_MAC_PJ_AT_VREF * (self.vdd / V_REF) ** 2
    def leak_w(self, tj_c): return self.dies * LEAK_W_25C * 2 ** ((tj_c - 25) / LEAK_DOUBLE_C)
    def power_w(self, util, tj_c=105.0):
        dyn = self.dies * self.n_mac * self.f_ghz * 1e9 * util * self.e_mac_pj() * 1e-12
        return dyn + self.dies * UNCORE_W + self.leak_w(tj_c)
    def tops_per_w_dense(self, util=1.0, tj_c=105.0):
        return self.tops_dense() * util / self.power_w(util, tj_c)


@dataclass
class Workload:
    name: str
    gmac: float        # dense GMAC per inference / per token
    weights_mb: float  # bytes of weights at the deployed precision
    act_mb: float      # peak live activation footprint
    util: float        # realistic MAC utilization for this class
    rate_hz: float     # required inferences (or tokens) per second
    safety: bool       # on the ASIL path?


WORKLOADS = [
    Workload("DMS: BlazeFace + alertness head, 640x480", 0.2, 0.5, 2, 0.15, 30, True),
    Workload("YOLOv8-m, 1280x736, one camera", 91, 26, 40, 0.65, 30, True),
    Workload("YOLOv8-m, 1280x736, six cameras", 91 * 6, 26, 40, 0.65, 30, True),
    Workload("BEV fusion, 6 cameras (BEVFormer-base class)", 650, 69, 120, 0.40, 10, True),
    Workload("Radar+lidar occupancy net (PointPillars class)", 60, 20, 60, 0.35, 20, True),
    Workload("LLM 8B INT4 decode, batch 1 (per token)", 8, 4000, 50, 0.60, 30, False),
    Workload("LLM 8B INT4 prefill, 512 tokens", 4096, 4000, 200, 0.60, 1, False),
    Workload("Image generation, SD1.5 class, 20 steps", 8000, 900, 300, 0.50, 0.1, False),
]

CONFIGS = [
    Config("A: Rev 1.3 as written (24,576 MAC @ 3.2 GHz)", 24576, 3.2, 128, 512, 6400),
    Config("B: Rev 1.3 at an achievable clock (24,576 MAC @ 2.0 GHz)", 24576, 2.0, 128, 512, 6400),
    Config("C: v2.0 baseline (65,536 MAC @ 2.0 GHz, 384-bit LPDDR5X)", 65536, 2.0, 128, 384, 8533),
    Config("D: v2.0 two-die package (2 x C over UCIe)", 65536, 2.0, 128, 384, 8533, dies=2),
]


def latency_ms(cfg: Config, w: Workload):
    """First-order latency: max(compute, memory). Weights stream from DRAM if they
    do not fit in SRAM; activations spill if they do not fit beside the weights."""
    compute_s = w.gmac * 1e9 / (cfg.dies * cfg.n_mac * cfg.f_ghz * 1e9 * w.util)
    sram_left = cfg.sram_mb - w.weights_mb
    traffic_mb = 0.0
    if w.weights_mb > cfg.sram_mb:
        traffic_mb += w.weights_mb
        sram_left = cfg.sram_mb
    if w.act_mb > max(sram_left, 0):
        traffic_mb += 2 * w.act_mb
    memory_s = traffic_mb * 1e6 / (cfg.dram_eff_gbps() * 1e9)
    return compute_s * 1e3, memory_s * 1e3, max(compute_s, memory_s) * 1e3


def section(title):
    print("\n" + "=" * 96)
    print(title)
    print("=" * 96)


def main():
    out = []
    section("1. Rev 1.3 headline claims vs arithmetic (config A: 24,576 MACs at 3.2 GHz)")
    A = CONFIGS[0]
    dense = A.tops_dense()
    print(f"Dense INT8 peak        : {dense:7.1f} TOPS   (24,576 MAC x 2 ops x 3.2 GHz)")
    print(f"2:4-sparse peak        : {dense*SPARSE_2_4:7.1f} TOPS   (industry-comparable number)")
    print(f"Claimed 1258 TOPS      : needs a sparsity factor of {1258/dense:4.2f}x  -> this is the 8:1 number")
    print(f"Claimed 2516 TOPS INT4 : dense INT4 is {A.tops_dense(4):7.1f} TOPS; 2516 again needs 8:1")
    print(f"DRAM 512-bit x 6400 MT/s = {A.dram_gbps():6.1f} GB/s raw  (claim 400 GB/s: consistent)")
    eff_32 = A.tops_per_w_dense(1.0, 105)
    print(f"Dense efficiency @3.2 GHz, 100% util, Tj 105 C : {eff_32:4.2f} TOPS/W "
          f"(claim 15-30 TOPS/W needs {15/eff_32:3.1f}-{30/eff_32:3.1f}x -> sparse units again)")
    p_full = A.power_w(1.0, 105)
    print(f"Power at 3.2 GHz, 100% util, Tj 105 C          : {p_full:5.1f} W  (claim: 40-50 W typical, 70-90 W peak)")
    print(f"Leakage of an un-gated die at Tj 125 C          : {A.leak_w(125):5.1f} W  (claim: 1-5 W always-on mode)")

    section("2. Thermal: maximum dissipation for Tj_max = 125 C by cooling method and ambient")
    cooling = [("Passive, sealed ECU", 2.5), ("Passive, large finned housing", 1.2),
               ("Forced air", 0.6), ("Cold plate / liquid", 0.25)]
    print(f"{'Cooling (R_theta_ja, C/W)':38s} {'Ta=65 C':>9s} {'Ta=85 C':>9s} {'Ta=105 C':>9s} {'Ta=125 C':>9s}")
    for name, r in cooling:
        row = [max(0.0, (TJ_MAX_C - ta) / r) for ta in (65, 85, 105, 125)]
        print(f"{name + f' ({r})':38s} " + " ".join(f"{x:8.1f}W" for x in row))
    print("Verdict: 70-90 W (or 40-50 W) 'passive cooling' is impossible above ~65 C ambient; at 125 C nothing dissipates.")

    section("3. Configuration comparison at the worst-case automotive corner")
    hdr = f"{'Config':62s} {'Dense':>7s} {'2:4':>7s} {'INT4':>7s} {'DRAM':>7s} {'P@60%':>7s} {'P@100%':>7s} {'TOPS/W':>7s}"
    print(hdr)
    print(f"{'':62s} {'TOPS':>7s} {'TOPS':>7s} {'2:4':>7s} {'GB/s':>7s} {'W':>7s} {'W':>7s} {'dense':>7s}")
    for c in CONFIGS:
        print(f"{c.name:62s} {c.tops_dense():7.0f} {c.tops_dense()*SPARSE_2_4:7.0f} "
              f"{c.tops_dense(4)*SPARSE_2_4:7.0f} {c.dram_gbps():7.0f} {c.power_w(0.6):7.1f} "
              f"{c.power_w(1.0):7.1f} {c.tops_per_w_dense(0.6):7.2f}")
    print("P@60% = typical ADAS duty (60% MAC utilization), Tj 105 C. Config A's clock is not reachable at this corner.")

    section("4. Per-workload latency and duty (first-order roofline), config C = v2.0 baseline")
    C = CONFIGS[2]
    print(f"{'Workload':50s} {'GMAC':>7s} {'compute':>8s} {'memory':>8s} {'latency':>8s} {'rate':>6s} {'duty':>6s} {'ASIL':>5s}")
    print(f"{'':50s} {'':>7s} {'ms':>8s} {'ms':>8s} {'ms':>8s} {'Hz':>6s} {'%':>6s} {'':>5s}")
    total_duty = 0.0
    for w in WORKLOADS:
        cm, mm, lat = latency_ms(C, w)
        duty = lat * w.rate_hz / 10.0  # percent of wall time
        if w.safety:
            total_duty += duty
        print(f"{w.name:50s} {w.gmac:7.1f} {cm:8.2f} {mm:8.2f} {lat:8.2f} {w.rate_hz:6.1f} {duty:6.1f} {'yes' if w.safety else 'no':>5s}")
    print(f"Safety-path duty if all ASIL workloads run concurrently: {total_duty:5.1f}% of config C "
          f"(needs < 70% to leave margin for redundant inference and LBIST).")
    tok = next(w for w in WORKLOADS if "decode" in w.name)
    cm, mm, lat = latency_ms(C, tok)
    print(f"LLM decode: {1000/lat:5.1f} tokens/s ceiling, MAC utilization {100*cm/mm:4.1f}% "
          f"(claim: '<0.5 ms' and '>90% utilization for LLMs')")

    section("5. Same workloads on config A (Rev 1.3 as written, if 3.2 GHz were achievable)")
    for w in WORKLOADS:
        cm, mm, lat = latency_ms(A, w)
        print(f"{w.name:50s} latency {lat:8.2f} ms   (claim: < 0.5 ms)")

    section("6. v2.0 baseline summary (config C)")
    print(f"Compute   : {C.n_mac:,} INT8 MACs = 64 cores x 1024 (32x32 systolic), {C.f_ghz} GHz worst-case")
    print(f"Peak      : {C.tops_dense():.0f} TOPS dense INT8/FP8, {C.tops_dense()*2:.0f} TOPS 2:4-sparse, "
          f"{C.tops_dense(4)*2:.0f} TOPS INT4 2:4-sparse")
    print(f"Memory    : {C.sram_mb:.0f} MB SRAM, {C.dram_bits}-bit LPDDR5X-{C.dram_mtps:.0f} = {C.dram_gbps():.0f} GB/s raw")
    print(f"Power     : {C.power_w(0.6):.0f} W typical (60% util, Tj 105 C), {C.power_w(1.0):.0f} W at 100% util; "
          f"TDP class 65 W")
    print(f"Efficiency: {C.tops_per_w_dense(0.6):.1f} TOPS/W dense at typical duty, "
          f"{C.tops_per_w_dense(0.6)*2:.1f} TOPS/W in 2:4-sparse units")
    print(f"Two-die D : {CONFIGS[3].tops_dense():.0f} TOPS dense, {CONFIGS[3].tops_dense()*2:.0f} TOPS 2:4-sparse, "
          f"{CONFIGS[3].power_w(0.6):.0f} W typical")

    section("7. SKU family from one die (cores fused off at test; gated cores leak ~20% of their share)")
    print(f"{'SKU':28s} {'cores':>5s} {'MACs':>7s} {'Dense':>6s} {'2:4':>6s} {'DRAM':>9s} {'P typ':>6s} {'TDP':>5s} {'Segment'}")
    print(f"{'':28s} {'':>5s} {'':>7s} {'TOPS':>6s} {'TOPS':>6s} {'bits/GB/s':>9s} {'W':>6s} {'W':>5s}")
    skus = [("Neo 262 (full die)", 64, 384, 11.0, 65, "Premium L2+/L3 central-compute companion"),
            ("Neo 196", 48, 384, 10.0, 45, "L2+ surround ADAS, hands-off highway"),
            ("Neo 131", 32, 256, 8.0, 30, "L2+ front + surround, forced-air ECU"),
            ("Neo 65 (yield recovery)", 16, 256, 8.0, 25, "L2 front camera + DMS; still ~21 W: not a passive part")]
    for name, cores, bits, uncore, tdp, seg in skus:
        c = Config(name, cores * 1024, 2.0, 128, bits, 8533)
        dyn = c.n_mac * c.f_ghz * 1e9 * 0.6 * c.e_mac_pj() * 1e-12
        leak = LEAK_W_25C * 2 ** ((105 - 25) / LEAK_DOUBLE_C) * (0.2 + 0.8 * cores / 64)
        p = dyn + uncore + leak
        print(f"{name:28s} {cores:5d} {c.n_mac:7,d} {c.tops_dense():6.0f} {c.tops_dense()*2:6.0f} "
              f"{bits:3d}/{c.dram_gbps():4.0f} {p:6.1f} {tdp:5d} {seg}")
    print("Same package, same software, one qualification; price tiers by cores enabled. The 16-core bin is still ~21 W (uncore + die leakage), so the passive-cooled mandate segment needs a small die, not a bin.")
    print("Gen 2 small die for the mandate segment (AEBS/LDWS/DDAWS): 16 cores, 2 x 64-bit LPDDR5X, ~75 mm2, ~12 W, "
          "reusing the core, NoC, island and compiler.")


if __name__ == "__main__":
    main()
