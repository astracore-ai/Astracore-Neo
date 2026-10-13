"""flops.py -- the flop population of a fault campaign build, from the RTL itself (drop 0.43, stream D2).

neosim (sim/neosim.py) elaborates the campaign's toplevel at the build's parameters -- the same generate expansion and
instance names Verilator gives the model's scopes (label[index] per generate iteration, the instance names) -- and every
signal a rising-edge always_ff writes is a flop; its elements (an unpacked array's entries) and bits are the population.
The memory leaves' arrays (`mem` of neo_mem_sp / neo_mem_rf: the SRAM macros, priced and protected as memories in the
FMEDA, not as flops) are listed apart, one entry per array (kind `sram`, block <parent>.sram, its word count): the
campaign samples their bits with a quota of their own; the leaves' own output registers (rdata / rb_data, the port-B
address latch rb_addr_q of drop 0.43) are flops labelled `macro`. Falling-edge blocks (the testbench hooks) are not
flops of the silicon tile.

Each flop gets a block: the innermost enclosing instance whose module is one of BLOCK_MODULES (abft_checker and the
delay lines are named after the block they serve). The campaign samples bits from this list (uniformly over the bits,
or with per-block quotas from a weighting file) and the report aggregates by block.

  python3 faults/flops.py --config tile --out flops_tile.json     # (run from dv/cocotb; the RTL from ../../rtl)
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RTL_DIR = os.path.normpath(os.path.join(HERE, "..", "..", "..", "rtl"))
SIM_DIR = os.path.normpath(os.path.join(HERE, "..", "..", "..", "sim"))

CORE_FILES = ["delay_line.sv", "mac_pe.sv", "skew_in.sv", "deskew_out.sv", "systolic_array.sv", "abft_checker.sv", "neo_mem.sv",
              "neo_mac_core.sv", "act_feeder.sv", "acc_bank.sv", "neo_mac_core_v02.sv", "core_seq.sv", "ecc39.sv", "wbuf_mem.sv",
              "requant.sv", "neo_core.sv"]
TILE_FILES = CORE_FILES + ["host_if.sv", "noc_router.sv", "sram_bank.sv", "mbist.sv", "bank_bist_wrap.sv", "crc16_word.sv",
                           "crc16_beat.sv", "tile_nic.sv", "tile_dma.sv", "prog_mem.sv", "esm.sv", "link_pack.sv", "neo_tile.sv",
                           "tile_mesh.sv"]

# the build configurations of the campaign: the cocotb `tile` and `core` builds (02_dv/dv/cocotb/Makefile), their toplevel
# as the model has it, and the parameters the Makefile passes
CONFIGS = {
    "tile": dict(top="tile_mesh_cocotb", files=TILE_FILES, tb=["tile_mesh_cocotb.sv"],
                 params=dict(NX=2, NY=2, ROWS=16, COLS=8, ACC_ROWS=64, ABUF_DEPTH=256, WBUF_DEPTH=512, BANK_DEPTH=4096,
                             FETCH_TIMEOUT=8192, BIST_WORDS=16, WPF=1, NCHK=1)),
    "core": dict(top="neo_core", files=CORE_FILES, tb=[],
                 params=dict(ROWS=16, COLS=8, ACC_ROWS=64, ABUF_DEPTH=256, WBUF_DEPTH=512, DV_HOOKS=1, NCHK=4)),
}

# the report's blocks: the innermost enclosing instance of one of these modules names a flop's block
BLOCK_MODULES = ("act_feeder", "neo_mac_core", "acc_bank", "core_seq", "wbuf_mem", "requant", "neo_mac_core_v02", "neo_core",
                 "noc_router", "link_packer", "link_unpacker", "tile_nic", "tile_dma", "prog_mem", "host_if", "esm", "mbist",
                 "sram_bank", "bank_bist_wrap", "neo_tile", "abft_checker")
MEM_LEAVES = ("neo_mem_sp", "neo_mem_rf")


def _load_neosim():
    if SIM_DIR not in sys.path:
        sys.path.insert(0, SIM_DIR)
    import neosim                                     # noqa: E402
    return neosim


def _parses(neosim, path):
    try:
        neosim.load([path])
        return True
    except Exception:
        return False


def enumerate_flops(config):
    """[{path, width, elem, block, module, inst}] for every flop element of the configuration's toplevel; path is
    relative to the toplevel (the cocotb dut), elem the unpacked index tuple (or [])."""
    neosim = _load_neosim()
    cfg = CONFIGS[config]
    files = [os.path.join(RTL_DIR, f) for f in cfg["files"]]
    files += [os.path.normpath(os.path.join(HERE, "..", "..", "..", "tb", f)) for f in cfg["tb"]]
    inst_mod = {}

    class Elab(neosim.Design):
        def _elab_module(self, modname, params, path, port_conns, is_top=False):
            inst_mod[path] = modname
            return super()._elab_module(modname, params, path, port_conns, is_top)

    try:
        d = Elab(neosim.load(files))
        d.top_ports = {}
        d._elab_module(cfg["top"], dict(cfg["params"]), "top", None, is_top=True)  # no cells: the declarations and processes suffice
    except Exception:
        # a module the lists above do not name (an RTL file added since): every file of rtl/ neosim parses, and again
        inst_mod.clear()
        files = [f for f in (os.path.join(RTL_DIR, n) for n in sorted(os.listdir(RTL_DIR)) if n.endswith(".sv"))
                 if _parses(neosim, f)] + files[len(cfg["files"]):]
        d = Elab(neosim.load(files))
        d.top_ports = {}
        d._elab_module(cfg["top"], dict(cfg["params"]), "top", None, is_top=True)

    flops = {}
    for _clk, _rst, st in d.ff_blocks:
        for decl in neosim.stmt_targets(st):
            flops[decl.hier] = decl
    out = []
    for hier, decl in sorted(flops.items()):
        scope, name = hier.rsplit(".", 1)
        # the instance chain: every prefix of the scope that is an instance (generate scopes are not)
        parts = scope.split(".")
        chain = []
        for i in range(1, len(parts) + 1):
            p = ".".join(parts[:i])
            if p in inst_mod:
                chain.append((p, inst_mod[p]))
        leaf_path, leaf_mod = chain[-1]
        kind = "flop"
        if leaf_mod in MEM_LEAVES and name == "mem":
            kind = "sram"                             # the SRAM array itself: a memory target, block <parent>.sram
        block, binst = None, None
        for p, m in reversed(chain):
            if m in BLOCK_MODULES:
                block, binst = m, p
                break
        if block is None:
            block, binst = chain[0][1], chain[0][0]
        if block == "abft_checker":                   # the array's checker or the drain checker
            parent = next((m for p, m in reversed(chain) if m in BLOCK_MODULES and m != "abft_checker"), "?")
            block = f"{parent}.abft_checker"
        if leaf_mod in MEM_LEAVES:
            block = f"{block}.{'sram' if kind == 'sram' else 'macro'}"
        dims = decl.dims
        rel = hier[len("top."):]
        if kind == "sram":                            # one entry per array: its words are the elements (sampled as such)
            out.append(dict(path=rel, width=decl.width, elem=[], words=dims[0][1] if dims else 1, block=block,
                            module=leaf_mod, inst=binst[len("top"):].lstrip("."), kind="sram"))
            continue
        elems = [[]]
        for lo, size in dims:
            elems = [e + [lo + i] for e in elems for i in range(size)]
        for e in elems:
            out.append(dict(path=rel, width=decl.width, elem=e, block=block, module=leaf_mod, inst=binst[len("top"):].lstrip("."),
                            kind="flop"))
    return out


def node_of(path):
    """The mesh node of a tile-build flop path (u_mesh.g_y[Y].g_x[X]...): Y * 2 + X on the 2 x 2 mesh, else None."""
    if not path.startswith("u_mesh.g_y["):
        return None
    y = int(path[len("u_mesh.g_y["):].split("]", 1)[0])
    x = int(path.split(".g_x[", 1)[1].split("]", 1)[0])
    return y * 2 + x


def summary(flops):
    blocks = {}
    for f in flops:
        b = blocks.setdefault(f["block"], [0, 0])
        b[0] += 1
        b[1] += f["width"] * f.get("words", 1)
    return blocks


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", choices=sorted(CONFIGS), required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    flops = enumerate_flops(args.config)
    with open(args.out, "w") as f:
        json.dump(dict(config=args.config, params=CONFIGS[args.config]["params"], flops=flops), f)
    bl = summary(flops)
    print(f"{args.config}: {len(flops)} flop elements, {sum(v[1] for v in bl.values())} bits in {len(bl)} blocks")
    for b, (n, bits) in sorted(bl.items(), key=lambda kv: -kv[1][1]):
        print(f"  {b:32s} {n:7d} elements {bits:8d} bits")


if __name__ == "__main__":
    main()
