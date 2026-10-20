#!/usr/bin/env python3
"""
neo_quant.py -- post-training quantization (drop 0.44, stream D3) of an imported graph (neo_onnx.Graph) for the mesh.

What the hardware computes, and what this module decides
  - Weights: INT8 per output channel (scale s_w[c] = max |W[c]| / 127, symmetric), or INT4 per output channel
    (max |W[c]| / 7, the nibbles the NIC expands on a FETCH_W with arg 1) for the layers named in `int4`; a layer named in
    `sparse` must already be 2:4 along its input channels (pruning is training-side; prune_2_4 is the stand-in for tests)
    and is validated by neo_backend.check_2_4 after quantization (rounding keeps every zero), for the NCHK = 4 core.
  - Activations: INT8 per tensor, symmetric (zero point 0: the feeder's padding and the residual buffer are zeros), the
    scale from a calibration set by abs-max, percentile or MSE (calibrate). Tensors a layout operator connects (concat,
    split, resize, maxpool, reshape, transpose, identity) share one scale -- the mesh moves their bytes without
    arithmetic -- so a scale group takes the largest threshold of its members.
  - The layer's epilogue on the drain (conv_epilogue): the accumulator (INT32, real value acc * s_in * s_w[c]) goes through
    the vector unit when the layer has a bias, an activation other than ReLU, or a residual: MUL_SCALE with the
    per-column SC[c] (the input scale times the column's weight scale) and BI[c] (the bias), the SiLU / GELU table (its
    input range R = a power of two covering the calibrated pre-activation; 8 at least), BMUL K0 and ADD_ROW for a
    residual added after the activation (YOLO's bottleneck: x + act(conv(x))), or ADD_ROW then BMUL / LUT for one added
    before it; then the requantizer (mult / shift per column, zero point 0, ReLU flag) maps the unit's output scale to
    the tensor's INT8 scale. A layer with no bias, no residual and no activation (or ReLU) needs no program: the
    requantizer alone, per column. Every program is executed by model/vec_ref.VecUnit -- the bit-exact model of
    rtl/vec_unit.sv -- so the integer executor here (run_int) is the golden of the RTL run.
  - Reporting: accuracy_report gives per layer the cosine similarity, the SQNR and the max error in output LSB of the
    integer executor against the float graph, on the calibration set or any other.
Usage: python3 compiler/neo_quant.py (the self-check: the YOLOv8-m group at a 12 x 12 crop, INT8 / INT4 / 2:4 variants).
"""
import math
import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "model"))
import neo_onnx as no  # noqa: E402
import neo_vec as nv  # noqa: E402
import neo_backend as nb  # noqa: E402
import vec_ref as vr  # noqa: E402

RES_SH = 10                     # the residual's shift into the accumulator's units in ADD_ROW
LAYOUT_KINDS = ("concat", "split", "resize", "maxpool", "reshape", "transpose", "identity", "flatten", "unsqueeze", "squeeze")


# ---------------------------------------------------------------------------------------------------------------------
# weights
# ---------------------------------------------------------------------------------------------------------------------
def quant_weights(W, bits=8):
    """Per-output-channel symmetric quantization of W [cout, ...]: (integers, scales[cout]) with scale = max |W[c]| / qmax
    (qmax 127 at 8 bits, 7 at 4), values in [-qmax, qmax] (the INT4 nibble -8 is never used: the sum of a row's weights
    must stay the check weight's)."""
    W = np.asarray(W, dtype=np.float64)
    qmax = (1 << (bits - 1)) - 1
    flat = W.reshape(W.shape[0], -1)
    s = np.maximum(np.abs(flat).max(axis=1), 1e-12) / qmax
    q = np.clip(np.round(flat / s[:, None]), -qmax, qmax).astype(np.int64).reshape(W.shape)
    return q, s


# ---------------------------------------------------------------------------------------------------------------------
# activation calibration
# ---------------------------------------------------------------------------------------------------------------------
def threshold(samples, method="percentile", pct=99.99):
    """The clipping threshold T of a tensor from its samples (|x| values): abs-max, the percentile of |x|, or the T that
    minimises the mean squared quantization error over a grid of candidates (MSE)."""
    a = np.abs(np.asarray(samples, dtype=np.float64).reshape(-1))
    if a.size == 0 or a.max() == 0:
        return 1.0
    if method == "max":
        return float(a.max())
    if method == "percentile":
        return max(float(np.percentile(a, pct)), float(a.max()) * 1e-3)
    if method == "mse":
        best, best_t = None, float(a.max())
        for f in np.linspace(0.3, 1.0, 36):
            t = float(a.max()) * f
            s = t / 127.0
            q = np.clip(np.round(a / s), -127, 127) * s
            e = float(((a - q) ** 2).mean())
            if best is None or e < best:
                best, best_t = e, t
        return best_t
    raise ValueError(f"calibration method {method}")


def scale_groups(graph):
    """Tensors connected by layout operators share a scale: union-find over the graph; returns {tensor: group id}."""
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra
    for L in graph.layers:
        if L.kind in LAYOUT_KINDS:
            for x in L.inputs:
                for y in L.outputs:
                    union(x, y)
    return {t: find(t) for t in graph.shapes}


def calibrate(graph, feeds_list, method="percentile", pct=99.99):
    """Per-tensor scales from a list of input dictionaries: {tensor: scale}, every activation (the graph's inputs and every
    layer output) symmetric INT8 with scale T / 127; also the pre-activation thresholds {layer name: T} of the layers
    whose activation runs on the unit's table. Scale groups take the largest threshold of their members."""
    samples, pre = {}, {}
    for feeds in feeds_list:
        t = no.run_float(graph, feeds, keep_all=True, keep_pre=True)
        for name, v in t.items():
            if name.endswith(":pre"):
                pre.setdefault(name[:-4], []).append(np.abs(v).max())
            else:
                samples.setdefault(name, []).append(np.asarray(v, dtype=np.float64).reshape(-1))
    thr = {name: threshold(np.concatenate(v), method, pct) for name, v in samples.items()}
    groups = scale_groups(graph)
    gmax = {}
    for name, T in thr.items():
        g = groups.get(name, name)
        gmax[g] = max(gmax.get(g, 0.0), T)
    scales = {name: gmax[groups.get(name, name)] / 127.0 for name in thr}
    return scales, {k: float(max(v)) for k, v in pre.items()}


# ---------------------------------------------------------------------------------------------------------------------
# the quantized layer and its epilogue
# ---------------------------------------------------------------------------------------------------------------------
@dataclass
class Epilogue:
    """What the drain of one output tile runs: the vector configuration (None: the bypass), the requantizer's tables per
    column and the ReLU flag; the residual tensor (its rows fetched into the residual buffer) when there is one."""
    vec: Optional[nv.VecConfig]
    mult: List[int]
    shift: List[int]
    zp: List[int]
    relu: int = 0
    residual: Optional[str] = None
    notes: dict = field(default_factory=dict)


@dataclass
class QLayer:
    """A quantized conv / linear layer: integer weights [cout, cin, k, k] (a linear layer as a 1x1 conv over its tokens),
    per-channel scales, the bias in float, the activation scales in and out, the fused activation and residual."""
    name: str
    kind: str                       # conv | linear
    w: np.ndarray
    w_scale: np.ndarray
    bits: int
    sparse: bool
    bias: Optional[np.ndarray]
    s_in: float
    s_out: float
    act: Optional[str]
    k: int
    s: int
    p: int
    input: str
    output: str
    residual: Optional[str] = None  # a tensor added to the activation's output (YOLO's bottleneck) ...
    res_pre: bool = False           # ... or to the pre-activation (added before the activation)
    s_res: float = 0.0
    pre_max: float = 0.0            # the calibrated |pre-activation| (the table's range)
    cout: int = 0
    cin: int = 0

    @property
    def s_acc(self):
        return self.s_in * self.w_scale


def common_shift(scales, max_mult=32767):
    sh = 0
    while sh < 31 and max(scales) * (1 << (sh + 1)) <= max_mult:
        sh += 1
    return sh, [max(1, min(max_mult, int(round(s * (1 << sh))))) for s in scales]


def lut_range(pre_max):
    """The activation table's input range: a power of two >= 8 covering the calibrated pre-activation (the table's INT16
    input is x / (R / 2^15); its output in units of R / 2^15 too, so |y| <= R fits)."""
    R = 8.0
    while R < pre_max * 1.05 and R < 2048:
        R *= 2
    return R


def conv_epilogue(ql: QLayer, cols: int, nt: int) -> Epilogue:
    """The epilogue of output tile nt (columns nt * cols .. + cols - 1; columns past cout get neutral tables)."""
    c0 = nt * cols
    idx = [min(c0 + j, ql.cout - 1) for j in range(cols)]
    s_acc = [float(ql.s_acc[c]) for c in idx]
    bias = [float(ql.bias[c]) if ql.bias is not None else 0.0 for c in idx]
    act = ql.act
    needs_unit = ql.bias is not None or ql.residual is not None or act in ("silu", "gelu")
    if not needs_unit:
        rq = [nv.requant_for(s / ql.s_out) for s in s_acc]
        return Epilogue(None, [m for m, _, _ in rq], [sh for _, sh, _ in rq], [0] * cols, relu=int(act == "relu"))
    prog, k, lutv = [], [0, 0, 0, 0], None
    s_res = ql.s_res
    table = act in ("silu", "gelu")
    R = lut_range(ql.pre_max) if table else None
    s_lut = (R / 32768.0) if table else None
    # stage 1: the accumulator (and the bias) into the units s_z1
    if ql.residual is not None and ql.res_pre:
        s_z1 = s_res / (1 << RES_SH)
    elif table:
        s_z1 = s_lut
    else:
        s_z1 = ql.s_out / 256.0
    sh1, sc = common_shift([s / s_z1 for s in s_acc])
    bi = [int(round(b / s_z1)) for b in bias]
    prog.append(vr.vop(vr.OP_MUL_SCALE, sh=sh1))
    s_cur = s_z1
    if ql.residual is not None and ql.res_pre:
        prog.append(vr.vop(vr.OP_ADD_ROW, sh=RES_SH))
        if table:
            k0, shk = nv.fixed_mult(s_cur / s_lut)
            k[0] = k0
            prog.append(vr.vop(vr.OP_BMUL, a=0, ka=1, sh=shk))
            s_cur = s_lut
    if table:
        prog.append(vr.vop(vr.OP_LUT, sh=0))
        f = nv.silu if act == "silu" else nv.gelu
        lutv = vr.build_lut(f, s_lut, s_lut)
        s_cur = s_lut
    if ql.residual is not None and not ql.res_pre:
        s_z2 = s_res / (1 << RES_SH)
        k0, shk = nv.fixed_mult(s_cur / s_z2)
        k[0] = k0
        prog += [vr.vop(vr.OP_BMUL, a=0, ka=1, sh=shk), vr.vop(vr.OP_ADD_ROW, sh=RES_SH)]
        s_cur = s_z2
    prog.append(vr.vop(vr.OP_EMIT))
    m, sh, _ = nv.requant_for(s_cur / ql.s_out)
    vc = nv.VecConfig(prog, lutv=lutv, sc=sc, bi=bi, k=k, name=f"{act or 'linear'}{'+res' if ql.residual else ''}{'+bias' if ql.bias is not None else ''}")
    return Epilogue(vc, [m] * cols, [sh] * cols, [0] * cols, relu=int(act == "relu"), residual=ql.residual,
                    notes=dict(R=R, s_z1=s_z1, s_cur=s_cur))


# ---------------------------------------------------------------------------------------------------------------------
# the quantized network
# ---------------------------------------------------------------------------------------------------------------------
@dataclass
class QNet:
    graph: no.Graph
    scales: Dict[str, float]
    qlayers: Dict[str, QLayer]
    folded: Dict[str, str]          # an Add's output -> the conv layer whose epilogue adds the residual
    method: str

    @property
    def input(self):
        return next(iter(self.graph.inputs))

    def table(self):
        lines = [f"{'layer':<22} {'kind':<6} {'cin':>4} {'cout':>4} {'k':>2} {'s':>2} {'bits':>4} {'2:4':>3} {'s_in':>9} {'s_out':>9} {'act':<5} {'res':<4} {'max|w_s|':>9}"]
        for q in self.qlayers.values():
            lines.append(f"{q.name:<22} {q.kind:<6} {q.cin:>4} {q.cout:>4} {q.k:>2} {q.s:>2} {q.bits:>4} {'y' if q.sparse else '-':>3} "
                         f"{q.s_in:>9.3g} {q.s_out:>9.3g} {q.act or '-':<5} {'y' if q.residual else '-':<4} {q.w_scale.max():>9.3g}")
        return "\n".join(lines)


def quantize(graph, calib, method="percentile", pct=99.99, int4=(), sparse=(), scales=None) -> QNet:
    """The post-training quantization of a graph: weights per channel (INT4 for the layers in `int4`, 2:4-validated for
    those in `sparse`), activations from the calibration inputs `calib` (a list of feed dicts; `scales` given skips it),
    the residual Adds folded into the conv that feeds them."""
    if scales is None:
        scales, pre = calibrate(graph, calib, method, pct)
    else:
        _, pre = calibrate(graph, calib, method, pct)
    qlayers, folded = {}, {}
    convs = {L.name: L for L in graph.layers if L.kind in ("conv", "linear")}
    # residual adds: Add(a, b) with one operand a conv output consumed only by the Add -> that conv's epilogue
    for L in graph.layers:
        if L.kind != "add":
            continue
        for src in L.inputs:
            P = graph.layer_of(src)
            if P is not None and P.kind in ("conv", "linear") and len(graph.consumers(src)) == 1 and src not in graph.outputs and P.name not in folded.values():
                folded[L.outputs[0]] = P.name
                break
    for L in graph.layers:
        if L.kind not in ("conv", "linear"):
            continue
        W = L.params["w"]
        if L.kind == "linear":
            W = W.T[:, :, None, None]                                      # [N, K] -> [cout, cin, 1, 1]
            k, s, p = 1, 1, 0
        else:
            k, s, p = L.attrs["k"], L.attrs["s"], L.attrs["p"]
        bits = 4 if L.name in int4 else 8
        if L.name in sparse:
            nb.check_2_4(W)                                                # the validator on the float tensor the training flow left
        wq, ws = quant_weights(W, bits)
        if L.name in sparse:
            nb.check_2_4(wq)
        out = L.outputs[0]
        residual, s_res, res_pre = None, 0.0, False
        add_out = next((o for o, c in folded.items() if c == L.name), None)
        if add_out is not None:
            A = graph.layer_of(add_out)
            residual = next(x for x in A.inputs if x != out)
            s_res = scales[residual]
            out = add_out
        ql = QLayer(L.name, L.kind, wq, ws, bits, L.name in sparse, L.params.get("b"), scales[L.inputs[0]], scales[out],
                    L.attrs.get("act"), k, s, p, L.inputs[0], out, residual, res_pre, s_res, pre.get(L.name, 0.0),
                    cout=W.shape[0], cin=W.shape[1])
        qlayers[L.name] = ql
    return QNet(graph, scales, qlayers, folded, method)


# ---------------------------------------------------------------------------------------------------------------------
# the integer executor (the golden of the mesh)
# ---------------------------------------------------------------------------------------------------------------------
def run_epilogue(ql: QLayer, acc, res_q=None, cols=8):
    """acc [cout, ho, wo] INT32 -> INT8 [cout, ho, wo] through the epilogue of every output tile on the vector unit's
    model (rows = pixels, lanes = the tile's columns) and the requantizer's model."""
    cout, ho, wo = acc.shape
    M = ho * wo
    out = np.zeros((cout, ho, wo), dtype=np.int64)
    for nt in range((cout + cols - 1) // cols):
        ep = conv_epilogue(ql, cols, nt)
        c0 = nt * cols
        rows = [[int(acc[c0 + j, m // wo, m % wo]) if c0 + j < cout else 0 for j in range(cols)] for m in range(M)]
        if ep.vec is not None:
            vu = vr.VecUnit(cols, cols, max(M, 1))
            ep.vec.apply(vu)
            if ep.residual is not None:
                for m in range(M):
                    vu.write_rbuf(m, [int(res_q[c0 + j, m // wo, m % wo]) if c0 + j < cout else 0 for j in range(cols)])
            rows, _ = vu.run(rows)
        for m in range(M):
            for j in range(cols):
                if c0 + j < cout:
                    out[c0 + j, m // wo, m % wo] = nv.rq_model(rows[m][j], ep.mult[j], ep.shift[j], ep.zp[j], ep.relu)
    return out


def run_int(qn: QNet, x_q, cols=8):
    """Execute the quantized network on an INT8 input tensor ([1, C, H, W] or [T, D] integers): every tensor as integers,
    bit for bit what the mesh drains (the conv layers through run_epilogue; the layout operators on the bytes)."""
    g = qn.graph
    t = {qn.input: np.asarray(x_q, dtype=np.int64)}
    skip = set(qn.folded)
    for L in g.layers:
        if L.outputs[0] in skip:
            continue
        a = L.attrs
        if L.kind in ("conv", "linear"):
            ql = qn.qlayers[L.name]
            x = t[L.inputs[0]]
            if L.kind == "linear":
                xi = x.reshape(-1, x.shape[-1]).T[:, :, None]              # [K, T, 1]: tokens as pixels
                acc = nb.nc.direct_conv(xi, ql.w, 1, 0)                    # [N, T, 1]
            else:
                acc = nb.nc.direct_conv(x[0], ql.w, ql.s, ql.p)
            res_q = None
            if ql.residual is not None:
                res_q = t[ql.residual]
                res_q = res_q[0] if L.kind == "conv" else res_q.reshape(-1, res_q.shape[-1]).T[:, :, None]
            y = run_epilogue(ql, acc, res_q, cols)
            y = y[None] if L.kind == "conv" else y[:, :, 0].T.reshape(x.shape[:-1] + (ql.cout,))
            t[ql.output] = y
        elif L.kind == "maxpool":
            t[L.outputs[0]] = no.maxpool2d(t[L.inputs[0]][0].astype(np.float64), a["k"], a["s"], a["p"]).astype(np.int64)[None]
        elif L.kind == "concat":
            t[L.outputs[0]] = np.concatenate([t[n] for n in L.inputs], axis=a["axis"])
        elif L.kind == "split":
            x = t[L.inputs[0]]
            ax = a["axis"]
            sizes = a["split"] or [x.shape[ax] // a["num_outputs"]] * a["num_outputs"]
            for name, part in zip(L.outputs, np.split(x, np.cumsum(sizes)[:-1], axis=ax)):
                t[name] = part
        elif L.kind == "resize":
            x = t[L.inputs[0]]
            f = a["scale"] if "scale" in a else a["sizes"][-1] // x.shape[-1]
            t[L.outputs[0]] = x.repeat(f, axis=-2).repeat(f, axis=-1)
        elif L.kind in ("identity", "reshape", "transpose", "flatten", "unsqueeze", "squeeze"):
            t[L.outputs[0]] = no.run_float(no.Graph("", {}, [], [L], g.opset), {L.inputs[0]: t[L.inputs[0]]})[L.outputs[0]].astype(np.int64)
        elif L.kind == "add":                                              # an add not folded: both operands at scales
            xa, xb = (t[n] for n in L.inputs)
            sa, sb, so = qn.scales[L.inputs[0]], qn.scales[L.inputs[1]], qn.scales[L.outputs[0]]
            t[L.outputs[0]] = np.clip(np.round((xa * sa + xb * sb) / so), -128, 127).astype(np.int64)
        else:
            raise no.NeoOnnxError(f"{L.name}: kind {L.kind} is not executed in integers (the transformer operators go through neo_xfmr)")
    return t


def quantize_input(qn: QNet, x):
    s = qn.scales[qn.input]
    return np.clip(np.round(np.asarray(x, dtype=np.float64) / s), -128, 127).astype(np.int64)


def accuracy_report(qn: QNet, feeds_list, cols=8):
    """Per layer and for the outputs: cosine similarity, SQNR (dB) and max |error| in the tensor's INT8 LSB of the integer
    executor against the float graph, over the given inputs. Returns (rows, lines)."""
    acc = {}
    for feeds in feeds_list:
        ref = no.run_float(qn.graph, feeds, keep_all=True)
        got = run_int(qn, quantize_input(qn, feeds[qn.input]), cols)
        for name, v in got.items():
            if name not in ref or name == qn.input:
                continue
            f = ref[name].reshape(-1)
            q = (v * qn.scales[name]).reshape(-1)
            d = acc.setdefault(name, dict(dot=0.0, ff=0.0, qq=0.0, err=0.0, maxe=0.0))
            d["dot"] += float(f @ q); d["ff"] += float(f @ f); d["qq"] += float(q @ q)
            d["err"] += float(((f - q) ** 2).sum()); d["maxe"] = max(d["maxe"], float(np.abs(f - q).max() / qn.scales[name]))
    rows = []
    for name, d in acc.items():
        cos = d["dot"] / math.sqrt(max(d["ff"] * d["qq"], 1e-30))
        sqnr = 10 * math.log10(d["ff"] / max(d["err"], 1e-30))
        rows.append((name, cos, sqnr, d["maxe"], name in qn.graph.outputs))
    lines = [f"{'tensor':<22} {'cos':>8} {'SQNR dB':>8} {'max|err| LSB':>13}"]
    for name, cos, sqnr, maxe, is_out in rows:
        lines.append(f"{name:<22} {cos:>8.5f} {sqnr:>8.2f} {maxe:>13.2f}{'  (output)' if is_out else ''}")
    return rows, lines


# ---------------------------------------------------------------------------------------------------------------------
# the self-check
# ---------------------------------------------------------------------------------------------------------------------
def yolo_group_case(seed=44, h=12, w=12, n_calib=4):
    """The YOLOv8-m group at a crop with calibration inputs: (graph, model, calibration feeds)."""
    rng = np.random.default_rng(seed)
    model = no.build_yolov8m_group(rng, h=h, w=w)
    graph = no.import_model(model)
    calib = [{"x": rng.normal(0, 1.0, (1, 3, h, w))} for _ in range(n_calib)]
    return graph, model, calib, rng


def self_check(verbose=True):
    ok = True
    graph, model, calib, rng = yolo_group_case()
    test = [{"x": rng.normal(0, 1.0, (1, 3, 12, 12))} for _ in range(2)]
    names = [q for q in graph.layers if q.kind == "conv"]
    variants = [("INT8 percentile", dict(method="percentile")), ("INT8 mse", dict(method="mse")), ("INT8 max", dict(method="max")),
                ("INT4 weights (the C2f convs)", dict(method="percentile", int4=[L.name for L in names if L.name.startswith("b2.")]))]
    for tag, kw in variants:
        qn = quantize(graph, calib, **kw)
        rows, lines = accuracy_report(qn, test)
        out = [r for r in rows if r[4]][0]
        if verbose:
            print(f"{tag}: output cos {out[1]:.5f}, SQNR {out[2]:.2f} dB, max |err| {out[3]:.2f} LSB; per layer min cos {min(r[1] for r in rows):.5f}")
        ok = ok and out[1] > 0.99
    # the 2:4 path: the validator rejects the dense tensor, accepts the pruned one, the quantized result stays 2:4
    L = names[3]
    try:
        quantize(graph, calib, sparse=[L.name])
        ok = False
        print("FAIL: a dense layer marked sparse was accepted")
    except ValueError as e:
        if verbose:
            print(f"2:4 validator on the dense '{L.name}': {str(e)[:90]}")
    pruned = nb.prune_2_4(L.params["w"] * 1000.0) / 1000.0                 # the training flow's pruning stand-in, in float
    saved = L.params["w"]
    L.params["w"] = pruned
    qn = quantize(graph, calib, sparse=[L.name])
    nb.check_2_4(qn.qlayers[L.name].w)
    rows, _ = accuracy_report(qn, test)
    if verbose:
        print(f"2:4 on '{L.name}' (pruned, validated): output cos {[r for r in rows if r[4]][0][1]:.5f}")
    L.params["w"] = saved
    print("RESULT:", "ALL PASS" if ok else "FAIL")
    return ok


if __name__ == "__main__":
    sys.exit(0 if self_check() else 1)
