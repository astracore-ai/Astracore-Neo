#!/usr/bin/env python3
"""
neo_run.py -- the runtime over the chip top (drop 0.44, stream D3), in Python: the model of 04_software/neo_runtime.c,
transaction for transaction, and the `neo_run` command-line tool.

A network comes as a NetImage (compiler/neo_sched.py: the tensors to load, the steps -- each a set of tile programs the
mesh runs at once with their descriptors, requantizer tables and vector-unit writes -- and the read-backs with the
integer executor's expected words). The runtime drives the chip top through a Device: the AXI4-Lite control plane
(axil_w / axil_r / poll), the host bridge into the banks (host_w / host_r) and the memory behind the memory bridge
(mem_w / mem_r: the host writes its own DRAM). The sequence (the same in neo_runtime.c):
  init    ID / VERSION / CONFIG read (the geometry must be the image's), every tile's errors cleared (CTRL bit 1), the
          memory bridge's window base, the command queue reset and configured (a ring of 4 commands at command-memory word
          0, a completion ring of 4 at word 16, the images from word 32), the command interrupt unmasked, the queue enabled
  loads   every tensor into its home: the memory window (mem_w) or a bank through the host bridge (host_w, then a one-word
          read through the same bridge as the fence the integration note asks for)
  steps   per step: the LUT tables through the tiles' windows (256-entry tables: too large for an image), the step's
          image written into the command memory (neo_sched.entries_of: descriptors, cfg_m, requantizer tables, the vector
          unit's small tables and program, the DMA program, a telemetry clear per tile, the CTRL start broadcast), the
          command (mask, image word and entry count, tag = step + 1) into its ring slot, the doorbell (CQ_TAIL); then the
          completion polled on CC_TAIL, the interrupt's status read and cleared, the record read (tag, entries played,
          the error bit, the global timer), CC_HEAD advanced; every tile's telemetry snapshot read (12 counters) and its
          ERR_CAUSE checked (decoded by name). `mode="window"`: the same registers written through the tiles' windows,
          the start per tile, the DONE map polled -- the fallback without the queue.
  reads   every read-back through the host bridge (host_r) or from the memory (mem_r), compared with the expected words.
The TraceDevice records every transaction as a line ('W addr value', 'R addr tag', 'P addr mask want', 'H node word n
words', 'G node word n name', 'M word n words', 'N word n name'); 02_dv/sim/check_c_runtime.py compares the C runtime's
trace with it word for word, and the cocotb top suite's T12 / T13 replay it on the RTL.
Usage: python3 sw/neo_run.py --build two_layer|yolo [--image out.txt] [--trace out.trace] [--mode queue|window]
"""
import argparse
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.realpath(__file__))
for d in (os.path.join(HERE, "..", "03_models", "compiler"), os.path.join(HERE, "..", "compiler")):
    if os.path.isdir(d):
        sys.path.insert(0, d)
import neo_sched as ns  # noqa: E402

# the control plane (neo_host.h's NEO_TOP_*)
G = 0x20000
G_ID, G_VERSION, G_CONFIG, G_IRQ_STATUS, G_IRQ_MASK, G_DONE_LO = G + 0x000, G + 0x004, G + 0x008, G + 0x040, G + 0x044, G + 0x030
G_CQ_CTRL, G_CQ_RING, G_CQ_TAIL, G_CQ_HEAD, G_CC_RING, G_CC_HEAD, G_CC_TAIL, G_CQ_STATUS = (G + 0x0A0 + 4 * i for i in range(8))
G_BR = G + 0x100
G_CMEM = G + 0x1000
REG_CTRL, REG_ERR_CAUSE, REG_TEL_CTRL, REG_TEL = 0x00, 0x06, 0x40, 0x41
NEO_TEL_NCNT = 12
TEL_NAMES = ["cycles", "array_valid", "feeder_stall", "fetch_wait", "drain", "stall_n", "stall_e", "stall_s", "stall_w", "stall_l",
             "bank_rd", "bank_wr"]
CAUSE_NAMES = ["parity_err", "crc_err", "array_abft", "acc_abft", "ctrl_err", "seq_err", "rq_err", "ecc_ue", "ecc_ce", "wbuf_ce",
               "wbuf_ue", "rq_tbl_perr", "lost_err", "fetch_timeout", "bist_fail", "prog_ce", "ctrl_path_err", "iso_err", "watchdog"]
WIN_BASE_DEFAULT = 0xAB_CDEF_0040


def cause_str(v):
    return ", ".join(n for i, n in enumerate(CAUSE_NAMES) if (v >> i) & 1) or "none"


def tile_reg(n, reg):
    return 0x400 * n + 4 * reg


class Device:
    """What a platform provides. Reads return None on a recording device."""

    def axil_w(self, addr, val): raise NotImplementedError
    def axil_r(self, addr, tag): raise NotImplementedError
    def poll(self, addr, mask, want): raise NotImplementedError
    def host_w(self, node, word, words): raise NotImplementedError
    def host_r(self, node, word, n, name): raise NotImplementedError
    def mem_w(self, word, words): raise NotImplementedError
    def mem_r(self, word, n, name): raise NotImplementedError
    def note(self, text): pass


class TraceDevice(Device):
    """Records the transactions as lines; every read answers None (nothing to check)."""

    def __init__(self):
        self.lines = []

    def axil_w(self, addr, val):
        self.lines.append(f"W {addr:05x} {val & 0xFFFFFFFF:08x}")

    def axil_r(self, addr, tag):
        self.lines.append(f"R {addr:05x} {tag}")
        return None

    def poll(self, addr, mask, want):
        self.lines.append(f"P {addr:05x} {mask:08x} {want:08x}")
        return None

    def host_w(self, node, word, words):
        self.lines.append(f"H {node} {word} {len(words)} " + " ".join(f"{w & 0xFFFFFFFF:08x}" for w in words))

    def host_r(self, node, word, n, name):
        self.lines.append(f"G {node} {word} {n} {name}")
        return None

    def mem_w(self, word, words):
        self.lines.append(f"M {word} {len(words)} " + " ".join(f"{w & 0xFFFFFFFF:08x}" for w in words))

    def mem_r(self, word, n, name):
        self.lines.append(f"N {word} {n} {name}")
        return None

    def note(self, text):
        self.lines.append("# " + text)


class RuntimeError_(Exception):
    pass


class Runtime:
    def __init__(self, dev: Device, img: ns.NetImage, mode="queue", win_base=WIN_BASE_DEFAULT, telemetry=True):
        self.dev, self.img, self.mode, self.win_base, self.telemetry = dev, img, mode, win_base, telemetry
        self.geo = img.geo
        self.step_no = 0
        self.records = []                   # per step: (tag, status, timer)
        self.tel = {}                       # (step index, node) -> {counter: value}
        self.causes = {}
        self.results = {}                   # read-back name -> words (None on a trace device)
        self.mismatch = []

    # ---- helpers
    def live(self, v):
        return v is not None

    def check(self, cond, msg):
        if not cond:
            raise RuntimeError_(msg)

    # ---- init
    def init(self):
        d, g = self.dev, self.geo
        d.note("init")
        v = d.axil_r(G_ID, "id")
        if self.live(v):
            self.check(v == 0x4E454F54, f"ID 0x{v:08x}")
        d.axil_r(G_VERSION, "version")
        v = d.axil_r(G_CONFIG, "config")
        if self.live(v):
            nx, ny, rows, cols = v & 0xF, (v >> 4) & 0xF, (v >> 16) & 0xFF, (v >> 24) & 0xFF
            self.check((nx, ny, rows, cols) == (g.nx, g.ny, g.rows, g.cols),
                       f"CONFIG {nx}x{ny} tiles of {rows}x{cols}: the image is for {g.nx}x{g.ny} of {g.rows}x{g.cols}")
        for n in range(g.nt):
            d.axil_w(tile_reg(n, REG_CTRL), 2)                             # every cause cleared
        d.axil_w(G_BR + 0x0, self.win_base & 0xFFFFFFFF)
        d.axil_w(G_BR + 0x4, self.win_base >> 32)
        d.axil_w(G_IRQ_STATUS, 0x1F)
        if self.mode == "queue":
            d.axil_w(G_CQ_CTRL, 2)                                         # the queue reset
            d.axil_w(G_CQ_RING, (ns.CQ_LOG2 << 16) | 0)
            d.axil_w(G_CC_RING, (ns.CC_LOG2 << 16) | 16)
            d.axil_w(G_IRQ_MASK, 0x0F)                                     # the command cause unmasked
            d.axil_w(G_CQ_CTRL, 1)

    # ---- loads
    def load(self, ld: ns.Load):
        d = self.dev
        d.note(f"load {ld.name}: {len(ld.words)} words into {ld.home} at {ld.addr}")
        if ld.home == "mem":
            d.mem_w(ld.addr, ld.words)
        else:
            n = int(ld.home[4:])
            d.host_w(n, ld.addr, ld.words)
            d.host_r(n, ld.addr, 1, "fence")                               # the fence: a read after the writes on their path

    # ---- steps
    def run_step(self, st: ns.Step):
        d, g = self.dev, self.geo
        i = self.step_no
        self.step_no += 1
        d.note(f"step {i} {st.name}: tiles {[t.node for t in st.tiles]}")
        for t in st.tiles:
            for reg, val in t.win:
                d.axil_w(tile_reg(t.node, reg), val)
        if self.mode == "queue":
            ent = ns.entries_of(st.tiles, g)
            self.check(len(ent) <= ns.IMG_ENTRIES, f"{st.name}: {len(ent)} image entries")
            for j, (e0, v) in enumerate(ent):
                d.axil_w(G_CMEM + 4 * (ns.IMG_BASE + 2 * j), e0)
                d.axil_w(G_CMEM + 4 * (ns.IMG_BASE + 2 * j + 1), v & 0xFFFFFFFF)
            mask = sum(1 << t.node for t in st.tiles)
            nq_ = 1 << ns.CQ_LOG2
            slot = i % nq_
            for k, v in enumerate((mask & 0xFFFFFFFF, mask >> 32, (len(ent) << 16) | ns.IMG_BASE, i + 1)):
                d.axil_w(G_CMEM + 4 * (4 * slot + k), v)
            d.axil_w(G_CQ_TAIL, (i + 1) % nq_)                                # the doorbell
            d.poll(G_CC_TAIL, 0xFF, (i + 1) % nq_)                            # the completion
            v = d.axil_r(G_IRQ_STATUS, "irq")
            if self.live(v):
                self.check(v & 0x10, f"step {i}: no command interrupt (IRQ_STATUS 0x{v:x})")
            d.axil_w(G_IRQ_STATUS, 0x10)
            rec = [d.axil_r(G_CMEM + 4 * (16 + 4 * slot + k), f"rec.{i}.{k}") for k in range(4)]
            if self.live(rec[0]):
                self.check(rec[0] == i + 1, f"step {i}: record tag {rec[0]}")
                self.check(rec[1] >> 16 == len(ent) and not (rec[1] & 1), f"step {i}: record status 0x{rec[1]:08x} ({len(ent)} entries played expected, error bit clear)")
                self.records.append((rec[0], rec[1], rec[2] | (rec[3] << 32)))
            d.axil_w(G_CC_HEAD, (i + 1) % nq_)
        else:
            for t in st.tiles:
                for e0, v in ns.entries_of([t], g)[:-1]:                      # the tile's entries, no start
                    d.axil_w(tile_reg(t.node, e0 & 0xFF), v)
            for t in st.tiles:
                d.axil_w(tile_reg(t.node, REG_CTRL), 1)
            mask = sum(1 << t.node for t in st.tiles)
            d.poll(G_DONE_LO, mask, mask)
        for t in st.tiles:
            if self.telemetry:
                d.axil_w(tile_reg(t.node, REG_TEL_CTRL), 1)                  # the snapshot of this step's window
                vals = [d.axil_r(tile_reg(t.node, REG_TEL + k), f"tel.{i}.{t.node}.{k}") for k in range(NEO_TEL_NCNT)]
                if self.live(vals[0]):
                    self.tel[(i, t.node)] = dict(zip(TEL_NAMES, vals))
            v = d.axil_r(tile_reg(t.node, REG_ERR_CAUSE), f"cause.{i}.{t.node}")
            if self.live(v):
                self.causes[(i, t.node)] = v
                self.check(v == 0, f"step {i} {st.name}: tile {t.node} ERR_CAUSE 0x{v:05x} = {cause_str(v)}")

    # ---- read-backs
    def read(self, rd: ns.Readback):
        d = self.dev
        if rd.home == "mem":
            got = d.mem_r(rd.addr, len(rd.words), rd.name)
        else:
            got = d.host_r(int(rd.home[4:]), rd.addr, len(rd.words), rd.name)
        self.results[rd.name] = got
        if got is not None:
            exp = [w & 0xFFFFFFFF for w in rd.words]
            got = [w & 0xFFFFFFFF for w in got]
            if got != exp:
                bad = [k for k in range(len(exp)) if got[k] != exp[k]]
                self.mismatch.append(f"{rd.name}: {len(bad)}/{len(exp)} words differ, first at {rd.addr + bad[0]}: 0x{got[bad[0]]:08x} expected 0x{exp[bad[0]]:08x}")
        return got

    def run(self):
        self.init()
        for ld in self.img.loads:
            self.load(ld)
        for st in self.img.steps:
            self.run_step(st)
        for rd in self.img.reads:
            self.read(rd)
        return self


class TraceReplay:
    """What a live replay of a recorded trace checks (the cocotb top suite's T12 / T13 replay the trace on the RTL): the
    reads' values by their tags, as Runtime checks them -- the ID, the interrupt per completion, the record's tag and
    entries played, every cause clear -- and the telemetry per (step, tile)."""

    def __init__(self, img: ns.NetImage, mode="queue"):
        self.img, self.mode = img, mode
        self.entries = [len(ns.entries_of(st.tiles, img.geo)) for st in img.steps]
        self.tel, self.records, self.causes = {}, {}, {}

    def on_read(self, addr, tag, value):
        f = tag.split(".")
        if tag == "id":
            assert value == 0x4E454F54, f"ID 0x{value:08x}"
        elif tag == "irq":
            assert value & 0x10, f"no command interrupt (IRQ_STATUS 0x{value:x})"
        elif f[0] == "rec":
            i, k = int(f[1]), int(f[2])
            r = self.records.setdefault(i, [0, 0, 0, 0])
            r[k] = value
            if k == 0:
                assert value == i + 1, f"step {i}: record tag {value}"
            if k == 1:
                assert value >> 16 == self.entries[i] and not (value & 1), f"step {i}: record status 0x{value:08x} ({self.entries[i]} entries expected)"
        elif f[0] == "tel":
            i, node, k = int(f[1]), int(f[2]), int(f[3])
            self.tel.setdefault((i, node), {})[TEL_NAMES[k]] = value
        elif f[0] == "cause":
            i, node = int(f[1]), int(f[2])
            self.causes[(i, node)] = value
            assert value == 0, f"step {i} ({self.img.steps[i].name}): tile {node} ERR_CAUSE 0x{value:05x} = {cause_str(value)}"

    def expected(self, name):
        return next(rd.words for rd in self.img.reads if rd.name == name)

    def step_cycles(self):
        """Per step: the longest tile window (cycles), the array-valid cycles summed over its tiles, the step name."""
        out = []
        for i, st in enumerate(self.img.steps):
            tl = [self.tel[(i, t.node)] for t in st.tiles if (i, t.node) in self.tel]
            if tl:
                out.append((st.name, max(t["cycles"] for t in tl), sum(t["array_valid"] for t in tl), len(tl)))
        return out


# ---------------------------------------------------------------------------------------------------------------------
# the networks the tests build (deterministic by seed; no model file is committed)
# ---------------------------------------------------------------------------------------------------------------------
def build_two_layer(seed=9, geo=None):
    """T12's two-layer network: conv 20 -> 8, 3x3, bias + SiLU on a 6x6 input (two padded channel tiles of 8 at 16x8 -- a
    K-split over two tiles), then conv 8 -> 7, 1x1, bias (the requantizer's path with the bias on the unit); the input
    in tile 3's bank through the host bridge, the weights in the memory window, the output in tile 0's bank."""
    import neo_onnx as no
    import neo_quant as nq
    geo = geo or ns.MeshGeo()
    rng = np.random.default_rng(seed)
    from onnx import helper
    inits = {"w1": rng.normal(0, 1 / np.sqrt(20 * 9), (8, 20, 3, 3)), "b1": rng.normal(0, 0.1, 8),
             "w2": rng.normal(0, 1 / np.sqrt(8), (7, 8, 1, 1)), "b2": rng.normal(0, 0.1, 7)}
    nodes = [helper.make_node("Conv", ["x", "w1", "b1"], ["c1"], name="l1.conv", kernel_shape=[3, 3], strides=[1, 1], pads=[1, 1, 1, 1]),
             helper.make_node("Sigmoid", ["c1"], ["s1"], name="l1.sig"), helper.make_node("Mul", ["c1", "s1"], ["y1"], name="l1.silu"),
             helper.make_node("Conv", ["y1", "w2", "b2"], ["y"], name="l2.conv", kernel_shape=[1, 1], strides=[1, 1], pads=[0, 0, 0, 0])]
    model = no._model(nodes, [no._vi("x", (1, 20, 6, 6))], [no._vi("y", (1, 7, 6, 6))], inits, "two_layer")
    graph = no.import_model(model)
    calib = [{"x": rng.normal(0, 1.0, (1, 20, 6, 6))} for _ in range(3)]
    qn = nq.quantize(graph, calib)
    x = rng.normal(0, 1.0, (1, 20, 6, 6))
    x_q = nq.quantize_input(qn, x)
    homes = {"x": f"bank{geo.nt - 1}", "y": "bank0"}
    img = ns.schedule(qn, geo, x_q, homes=homes)
    img.meta["model"] = model
    img.meta["qn"] = qn
    img.meta["x"] = x
    return img


def build_yolo(seed=44, h=12, w=12, geo=None, int4=False, method="percentile"):
    """The YOLOv8-m layer group at an h x w crop: importer, PTQ (calibration on random inputs of the crop), schedule; the
    input and every tensor in the memory window."""
    import neo_quant as nq
    geo = geo or ns.MeshGeo()
    graph, model, calib, rng = nq.yolo_group_case(seed, h, w)
    kw = dict(method=method)
    if int4:
        kw["int4"] = [L.name for L in graph.layers if L.kind == "conv" and L.name.startswith("b2.")]
    qn = nq.quantize(graph, calib, **kw)
    x = rng.normal(0, 1.0, (1, 3, h, w))
    x_q = nq.quantize_input(qn, x)
    img = ns.schedule(qn, geo, x_q)
    img.meta["model"] = model
    img.meta["qn"] = qn
    img.meta["x"] = x
    img.meta["calib"] = calib
    return img


BUILDERS = {"two_layer": build_two_layer, "yolo": build_yolo, "yolo_int4": lambda **kw: build_yolo(int4=True, **kw)}


def main():
    ap = argparse.ArgumentParser(description="the Neo runtime in Python: build a network's image, record its transactions")
    ap.add_argument("--build", choices=sorted(BUILDERS), help="build one of the test networks")
    ap.add_argument("--load", help="an image file (neo_sched.NetImage text) instead of --build")
    ap.add_argument("--image", help="write the image here")
    ap.add_argument("--trace", help="write the transaction trace here")
    ap.add_argument("--mode", choices=("queue", "window"), default="queue")
    ap.add_argument("--no-telemetry", action="store_true")
    ap.add_argument("--summary", action="store_true", help="print the schedule's summary")
    a = ap.parse_args()
    if a.build:
        img = BUILDERS[a.build]()
    elif a.load:
        img = ns.NetImage.read(a.load)
    else:
        ap.error("--build or --load")
    if a.image:
        img.write(a.image)
    if a.summary:
        print(ns.summary(img))
    dev = TraceDevice()
    rt = Runtime(dev, img, mode=a.mode, telemetry=not a.no_telemetry).run()
    if a.trace:
        with open(a.trace, "w") as f:
            f.write("\n".join(dev.lines) + "\n")
    print(f"neo_run: {len(img.loads)} loads, {len(img.steps)} steps, {len(img.reads)} read-backs; {len(dev.lines)} transactions"
          + (f" -> {a.trace}" if a.trace else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
