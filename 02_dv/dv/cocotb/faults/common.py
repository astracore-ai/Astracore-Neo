"""common.py -- what the fault campaign's two drivers share (drop 0.43, stream D2): the injector, the fault list, the
classification and the CSV row.

Injection goes through the model's VPI (cocotb's Verilator builds pass --public-flat-rw, so every flop is a public,
writable variable): a flop is reached by its hierarchical path below the toplevel (through generate scopes in two steps,
Injector.resolve) and, for an unpacked array, its index. A deposit made in the ReadWrite phase after a rising edge holds the
register's value for that cycle, the combinational logic behind it re-evaluates (checked on Verilator 5.020: the fan-out of
a deposited flop is recomputed before the next edge samples it), and the register's own next state is computed from the
deposited value; right after RisingEdge (before ReadWrite) the edge is not evaluated yet and a read returns the old value.
Every register keeps its own storage in these builds (checked: a deposit into each copy of the lockstep / duplicated pairs
leaves the other copy unchanged), so the comparators between copies see the fault.
  flip  -- a single-cycle upset: the bit inverted once, after the edge that starts cycle `cycle` of the run (an SEU)
  sa0 / sa1 -- a stuck-at, from cycle `cycle` to the end of the run: the bit forced after every rising edge
Two-dimensional unpacked arrays are not reachable through cocotb's VPI on Verilator 5.020 (the program memory's words,
the router's FIFO slots, the NIC's partial-sum FIFO: Verilator's vpi_handle_by_index returns a memory word that cannot be
indexed again): flops.py lists them, the sampler leaves them out and the report says how many bits that is. The arrays
Injector.resolve cannot reach, and the unpacked arrays of single bits with no packed dimension (one GPI register on
Verilator 5.020: requant's wrt / par / n1 / n2, the router's valid bits), are probed by the fault-free run (FAULTS_PROBE,
the first element of each) and left out the same way.

Outcome of a run against the fault-free run of the same driver:
  detected      a cause was raised (any sticky flag or the watchdog), latency = the first cycle the flag was seen - the
                injection cycle; the run's results may be right (a corrected or a safe-side error) or wrong
  masked        the run finished, the results are bit-exact, no cause
  dangerous     the run finished, the results differ, no cause (or a cause later than the FTTI budget)
  hang_detected the run did not finish within the bound and a cause was raised (the watchdog or the fetch timeout)
  hang          the run did not finish and no cause was raised
The FTTI budget: detect within 10 ms (spec section 8) -- 20,000,000 cycles at 2 GHz, longer than any run here, so a
cause within the run is within the budget; the column is kept so a longer workload is classified correctly.
"""
import csv
import os
import time

from cocotb.handle import NonHierarchyIndexableObject, SimHandle

FTTI_CYCLES = 20_000_000            # 10 ms at 2.0 GHz (spec section 8: detect <= 10 ms)

CSV_FIELDS = ["id", "config", "workload", "node", "block", "flop", "bit", "type", "cycle", "outcome", "causes", "first_cause",
              "latency", "diff", "run_cycles", "wall_ms"]


class Injector:
    """Handles by (path, elem), cached; flip / force one bit."""

    def __init__(self, dut):
        self.dut = dut
        self.cache = {}
        self.scopes = {}

    def handle(self, path, elem=()):
        key = (path, tuple(elem))
        h = self.cache.get(key)
        if h is None:
            h = self.resolve(path)
            if not h._path.endswith("." + path):         # a handle that collapsed onto another object (see resolve)
                raise LookupError(f"{path} resolved to {h._path}")
            if isinstance(h, NonHierarchyIndexableObject) and len(h) == 0:
                raise LookupError(f"{path}: an array cocotb could not range (see resolve)")
            for i in elem:
                h = h[i]
            self.cache[key] = h
        return h

    def resolve(self, path):
        """The handle of `path` below the toplevel. A path through generate scopes (`g_y[1].g_x[0]...`) is looked up in
        two steps: cocotb 1.9's VPI layer counts the ']' in the requested name after the object's own name to pick an
        array's range, so an unpacked array asked for by its whole path (`u_mesh.g_y[1].g_x[0].u_t.u_nic.stage`) gets no
        range ("Unable to get range for indexable object"), is named "unknown" and every such handle collapses onto the
        first one cocotb created (cocotb's handle singletons) -- the four banks of the mesh were one bank. So the first
        module scope after the last indexed component is fetched by name (cocotb makes it a pseudo-region; Verilator's
        plain generate scopes do not map to a cocotb object at all, so the prefix grows until one does) and the rest of
        the path, which has no ']', is asked of it. A path without generate indices is asked of the toplevel. An array in
        a module instance that Verilator registers as a plain scope below a generate index (its module class shared with
        other instances: in the tile build the computing core's skew pipes, `u_core.g_vskew[i].g_vd.u_vd.pipe`) has no
        such module scope after the index and stays unreachable; `unreachable` lists those for run_faults.py, which
        leaves them out of the sample and reports their bits (vectors resolve by the whole path either way)."""
        parts = path.split(".")
        last = max((i for i, p in enumerate(parts) if "[" in p), default=-1)
        if last >= 0:
            for k in range(last + 1, len(parts)):
                pre = ".".join(parts[:k])
                if pre not in self.scopes:                # a miss is kept too (cocotb warns once per scope it cannot map)
                    self.scopes[pre] = self.dut._handle.get_handle_by_name(pre)
                raw = self.scopes[pre]
                if raw is None:
                    continue
                leaf = raw.get_handle_by_name(".".join(parts[k:]))
                if leaf is not None:
                    return SimHandle(leaf, self.dut._path + "." + path)
        return self.dut._id(path, extended=False)

    def unreachable(self, items):
        """the paths of `items` ([path, first element index]: the population's arrays) whose array does not resolve to a
        ranged array of its own or whose element cannot be indexed -- an unpacked array of single bits with no packed
        dimension (`logic wrt [COLS]`) is one GPI register of COLS bits on Verilator 5.020, with no elements to index"""
        bad = []
        for p, elem in items:
            try:
                self.handle(p, elem)
            except Exception:
                bad.append(p)
        return bad

    @staticmethod
    def flip(h, bit):
        h.value = int(h.value) ^ (1 << bit)

    @staticmethod
    def force(h, bit, val):
        v = int(h.value)
        nv = (v | (1 << bit)) if val else (v & ~(1 << bit))
        if nv != v:
            h.value = nv


class Fault:
    """One fault of the list (a dict from run_faults.py): path, elem, bit, type, cycle and its labels."""

    def __init__(self, d):
        self.d = d
        self.path, self.elem, self.bit = d["path"], d.get("elem", []), int(d["bit"])
        self.type, self.cycle = d["type"], int(d["cycle"])
        self.h = None

    def name(self):
        return self.path + "".join(f"[{i}]" for i in self.elem)

    def arm(self, inj):
        self.h = inj.handle(self.path, self.elem)

    def active(self, cycle):
        """whether the fault acts in the cycle that starts at this edge"""
        return cycle == self.cycle if self.type == "flip" else cycle >= self.cycle

    def apply(self, inj, cycle):
        """Called in the ReadWrite phase after the rising edge that starts `cycle`, when active(cycle). Right after
        RisingEdge the model has not evaluated the edge yet (a read returns the value before it), so the drivers await
        ReadWrite first: the read is the register's new value, the write lands after the edge and the logic behind the
        register re-evaluates before the next edge (checked on Verilator 5.020 with cocotb 1.9.2)."""
        if self.type == "flip":
            inj.flip(self.h, self.bit)
        else:
            inj.force(self.h, self.bit, 1 if self.type == "sa1" else 0)


def classify(finished, causes, first_det, cycle, diff):
    """(outcome, latency) from the run's facts: finished (bool), causes (list of names), first_det (the cycle a cause was
    first seen, or None), the injection cycle, diff (the number of result words that differ)."""
    latency = (first_det - cycle) if first_det is not None else ""
    if not finished:
        return ("hang_detected" if causes else "hang"), latency
    if causes:
        if first_det is not None and first_det - cycle > FTTI_CYCLES:
            return "dangerous", latency
        return "detected", latency
    return ("masked" if diff == 0 else "dangerous"), latency


class CsvOut:
    def __init__(self, path):
        new = not os.path.exists(path)
        self.f = open(path, "a", newline="")
        self.w = csv.DictWriter(self.f, fieldnames=CSV_FIELDS)
        if new:
            self.w.writeheader()
            self.f.flush()

    def row(self, **kw):
        self.w.writerow({k: kw.get(k, "") for k in CSV_FIELDS})
        self.f.flush()

    def close(self):
        self.f.close()


class Budget:
    """The worker's wall-clock budget (FAULTS_BUDGET_S): the list is worked through until it runs out, so the job ends in
    its time limit with every finished fault in the CSV."""

    def __init__(self):
        self.t0 = time.time()
        self.limit = float(os.environ.get("FAULTS_BUDGET_S", "0") or 0)

    def left(self):
        return (self.limit <= 0) or (time.time() - self.t0 < self.limit)
