#!/usr/bin/env python3
"""
neo_onnx.py -- the ONNX importer (drop 0.44, stream D3): an ONNX graph (opset 17) to the compiler's layer list.

What it does
  1. Reads a ModelProto (a path or the object), checks the opset, takes the initializers and Constant nodes as constants,
     and walks the nodes in graph order. Every node either becomes a Layer, is fused into the Layer of the node before it
     (a Conv's bias Add, its ReLU, its SiLU = Sigmoid + Mul; a MatMul's bias Add; the Erf form of GELU; the Pow /
     ReduceMean / Sqrt / Div / Mul form of an RMS norm; a Softmax's additive mask; a MatMul's scalar scale), or is reported
     as unsupported with its node name and operator (NeoOnnxError lists every one, or `strict=False` keeps the report in
     Graph.unsupported and the graph is still executable for the operators it has).
  2. Infers every tensor's shape by executing the graph on zeros (the shapes of a static graph are what the execution
     produces; onnx.shape_inference is consulted where it answers, and a disagreement is an error).
  3. Executes the graph in float (run_float): the reference every quantized form is measured against, checked against
     onnxruntime on the small per-family models test_neo_onnx.py generates (build_* below make them: no ONNX file is
     committed).
  4. Maps the layers onto the compiler: conv_layers() gives neo_compile.Conv shapes of the convolutions (the perf model);
     block_from_graph() recognises a transformer block (norm, Q / K / V, Q K^T with its scale and causal mask, softmax, P V,
     the output projection and residual, norm, the MLP with GELU / SiLU and its residual) and returns neo_xfmr's BlockCfg
     with its weights and gains, so neo_xfmr.block_plan compiles the ONNX block exactly as M23 / M24 run it; the
     convolution networks go through neo_quant (quantization) and neo_sched (programs and the runtime's image).

Operators (opset 17, the default domain): Conv (group 1, dilation 1, symmetric pads; fused bias / Relu / SiLU /
Sigmoid-mul), MaxPool, Concat, Split, Resize (nearest, integer scales), MatMul, Gemm, Softmax, LayerNormalization, Gelu
(the operator of any domain, or the Erf form), Add (a residual, a bias, a mask), Transpose, Reshape, Flatten, Identity,
Relu, Sigmoid, Mul / Div (SiLU and scalar scales), the RMS-norm form (Pow, ReduceMean, Add, Sqrt, Div, Mul), Constant,
Unsqueeze / Squeeze (static). Tensors are float32, batch 1 for the convolution path; NCHW.
"""
import math
import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

OPSET_MAX = 17
SUPPORTED = ("Conv", "MaxPool", "Concat", "Split", "Resize", "MatMul", "Gemm", "Softmax", "LayerNormalization", "Gelu", "Add",
             "Transpose", "Reshape", "Flatten", "Identity", "Relu", "Sigmoid", "Mul", "Div", "Sub", "Pow", "ReduceMean", "Sqrt",
             "Erf", "Constant", "Unsqueeze", "Squeeze")


class NeoOnnxError(Exception):
    """An unsupported operator or an unsupported form of a supported one; the message names the node."""


@dataclass
class Layer:
    """One operator of the imported graph after fusion. `inputs` are activation tensor names (constants are in `params`);
    `nodes` the ONNX node names it absorbed (the first is its own)."""
    name: str
    kind: str
    inputs: List[str]
    outputs: List[str]
    attrs: dict = field(default_factory=dict)
    params: Dict[str, np.ndarray] = field(default_factory=dict)
    nodes: List[str] = field(default_factory=list)
    in_shapes: List[Tuple[int, ...]] = field(default_factory=list)
    out_shapes: List[Tuple[int, ...]] = field(default_factory=list)

    @property
    def output(self):
        return self.outputs[0]

    def describe(self):
        a = self.attrs
        if self.kind == "conv":
            w = self.params["w"]
            s = f"{w.shape[1]}->{w.shape[0]} k{a['k']} s{a['s']} p{a['p']}"
            if a.get("act"):
                s += f" +{a['act']}"
            if "b" in self.params:
                s += " +bias"
            return s
        if self.kind == "linear":
            w = self.params["w"]
            return f"{w.shape[0]}->{w.shape[1]}" + (" +bias" if "b" in self.params else "") + (f" +{a['act']}" if a.get("act") else "")
        return " ".join(f"{k}={v}" for k, v in a.items() if not isinstance(v, np.ndarray))


@dataclass
class Graph:
    name: str
    inputs: Dict[str, Tuple[int, ...]]
    outputs: List[str]
    layers: List[Layer]
    opset: int
    unsupported: List[Tuple[str, str, str]] = field(default_factory=list)      # (node name, op type, reason)
    shapes: Dict[str, Tuple[int, ...]] = field(default_factory=dict)

    def layer_of(self, tensor):
        for L in self.layers:
            if tensor in L.outputs:
                return L
        return None

    def consumers(self, tensor):
        return [L for L in self.layers if tensor in L.inputs]

    def table(self):
        """The operator coverage of this graph: one line per layer."""
        lines = [f"{'layer':<28} {'kind':<10} {'input':<22} {'output':<22} {'params'}"]
        for L in self.layers:
            lines.append(f"{L.name[:28]:<28} {L.kind:<10} {'x'.join(map(str, L.in_shapes[0])) if L.in_shapes else '?':<22} "
                         f"{'x'.join(map(str, L.out_shapes[0])) if L.out_shapes else '?':<22} {L.describe()}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------------------------------------------------
# reading the model
# ---------------------------------------------------------------------------------------------------------------------
def _attrs(node):
    from onnx import helper
    return {a.name: helper.get_attribute_value(a) for a in node.attribute}


def _np(tensor):
    from onnx import numpy_helper
    return numpy_helper.to_array(tensor)


def load_model(model):
    import onnx
    if isinstance(model, (str, bytes, os.PathLike)):
        model = onnx.load(model)
    return model


def import_model(model, strict=True) -> Graph:
    """The importer. `model`: a path or a ModelProto. Returns the Graph; raises NeoOnnxError on an unsupported operator
    (strict) or records it (strict=False)."""
    import onnx
    model = load_model(model)
    opset = next((o.version for o in model.opset_import if o.domain in ("", "ai.onnx")), None)
    if opset is None or opset > OPSET_MAX:
        raise NeoOnnxError(f"opset {opset}: the importer takes the default domain at opset <= {OPSET_MAX}")
    g = model.graph
    consts = {t.name: _np(t) for t in g.initializer}
    init_names = set(consts)
    inputs = {}
    for vi in g.input:
        if vi.name in init_names:
            continue
        dims = tuple(d.dim_value if d.HasField("dim_value") else 1 for d in vi.type.tensor_type.shape.dim)
        inputs[vi.name] = dims
    outputs = [vi.name for vi in g.output]
    nodes = list(g.node)
    for i, n in enumerate(nodes):
        if not n.name:
            n.name = f"{n.op_type}_{i}"
    producer = {}
    consumers = {}
    for n in nodes:
        for o in n.output:
            producer[o] = n
        for x in n.input:
            consumers.setdefault(x, []).append(n)
    graph = Graph(g.name or "onnx", inputs, outputs, [], opset)
    for n in nodes:                                                         # Constant nodes before the walk: a fusion looks ahead
        if n.op_type == "Constant":
            a = _attrs(n)
            consts[n.output[0]] = _np(a["value"]) if "value" in a else np.array(a.get("value_float", a.get("value_int")))
    absorbed = set()                                                       # node names fused into an earlier layer

    def const(name):
        return consts.get(name)

    def is_const(name):
        return name in consts

    def sole_consumer(tensor, op=None):
        cs = consumers.get(tensor, [])
        if len(cs) == 1 and (op is None or cs[0].op_type == op) and tensor not in outputs:
            return cs[0]
        return None

    def absorb(node, layer, out_tensor):
        absorbed.add(node.name)
        layer.nodes.append(node.name)
        layer.outputs = [out_tensor]

    def fail(node, reason):
        if strict:
            raise NeoOnnxError(f"node '{node.name}' ({node.op_type}): {reason}")
        graph.unsupported.append((node.name, node.op_type, reason))

    def act_fusion(layer, out):
        """ReLU, or SiLU as Sigmoid + Mul (either input order), after a conv / linear output `out`."""
        nxt = sole_consumer(out)
        cs = consumers.get(out, [])
        if nxt is not None and nxt.op_type == "Relu":
            layer.attrs["act"] = "relu"
            absorb(nxt, layer, nxt.output[0])
            return nxt.output[0]
        if len(cs) == 2 and out not in outputs:
            sig = next((c for c in cs if c.op_type == "Sigmoid"), None)
            mul = next((c for c in cs if c.op_type == "Mul"), None)
            if sig is not None and mul is not None and sig.output[0] in mul.input and sole_consumer(sig.output[0]) is mul:
                layer.attrs["act"] = "silu"
                absorb(sig, layer, out)
                absorb(mul, layer, mul.output[0])
                return mul.output[0]
        return out

    for n in nodes:
        if n.name in absorbed:
            continue
        op, a = n.op_type, _attrs(n)
        if op == "Constant":
            continue
        if op not in SUPPORTED:
            fail(n, "operator not supported by the importer")
            continue
        L = Layer(n.name, "?", [x for x in n.input if x and not is_const(x)], list(n.output), nodes=[n.name])
        try:
            if op == "Conv":
                w = const(n.input[1])
                if w is None:
                    raise NeoOnnxError("the weights are not constant")
                k = tuple(a.get("kernel_shape", w.shape[2:]))
                s = tuple(a.get("strides", (1, 1)))
                p = tuple(a.get("pads", (0, 0, 0, 0)))
                d = tuple(a.get("dilations", (1, 1)))
                if a.get("group", 1) != 1:
                    raise NeoOnnxError(f"group {a['group']}: grouped / depthwise convolutions are not lowered (the core is dense over K)")
                if d != (1, 1):
                    raise NeoOnnxError(f"dilations {d}: only 1")
                if k[0] != k[1] or s[0] != s[1] or not (p[0] == p[1] == p[2] == p[3]):
                    raise NeoOnnxError(f"kernel {k} strides {s} pads {p}: square kernels, equal strides and symmetric pads")
                if a.get("auto_pad", "NOTSET") not in ("NOTSET", b"NOTSET"):
                    raise NeoOnnxError("auto_pad: explicit pads only")
                L.kind = "conv"
                L.attrs = dict(k=int(k[0]), s=int(s[0]), p=int(p[0]))
                L.params["w"] = w.astype(np.float64)
                if len(n.input) > 2 and n.input[2]:
                    L.params["b"] = const(n.input[2]).astype(np.float64).reshape(-1)
                out = n.output[0]
                nxt = sole_consumer(out, "Add")
                if nxt is not None:
                    other = [x for x in nxt.input if x != out]
                    bc = const(other[0]) if other and is_const(other[0]) else None
                    if bc is not None and bc.size == w.shape[0]:
                        L.params["b"] = L.params.get("b", 0.0) + bc.astype(np.float64).reshape(-1)
                        absorb(nxt, L, nxt.output[0])
                        out = nxt.output[0]
                act_fusion(L, out)
            elif op == "MaxPool":
                k = tuple(a["kernel_shape"]); s = tuple(a.get("strides", (1, 1))); p = tuple(a.get("pads", (0, 0, 0, 0)))
                if k[0] != k[1] or s[0] != s[1] or not (p[0] == p[1] == p[2] == p[3]) or a.get("ceil_mode", 0):
                    raise NeoOnnxError(f"kernel {k} strides {s} pads {p}: square windows, equal strides, symmetric pads, floor mode")
                L.kind = "maxpool"
                L.attrs = dict(k=int(k[0]), s=int(s[0]), p=int(p[0]))
            elif op == "Concat":
                L.kind = "concat"
                L.attrs = dict(axis=int(a["axis"]))
            elif op == "Split":
                L.kind = "split"
                split = const(n.input[1]) if len(n.input) > 1 and n.input[1] else a.get("split")
                L.attrs = dict(axis=int(a.get("axis", 0)), split=[int(v) for v in split] if split is not None else None,
                               num_outputs=len(n.output))
            elif op == "Resize":
                mode = a.get("mode", b"nearest")
                mode = mode.decode() if isinstance(mode, bytes) else mode
                if mode != "nearest":
                    raise NeoOnnxError(f"mode {mode}: nearest only")
                scales = const(n.input[2]) if len(n.input) > 2 and n.input[2] else None
                sizes = const(n.input[3]) if len(n.input) > 3 and n.input[3] else None
                if scales is not None and scales.size:
                    sc = [float(v) for v in scales]
                    if sc[:2] != [1.0, 1.0] or any(abs(v - round(v)) > 1e-9 or v < 1 for v in sc[2:]):
                        raise NeoOnnxError(f"scales {sc}: integer upsampling of H and W only")
                    L.attrs = dict(scale=int(round(sc[2])))
                    if int(round(sc[2])) != int(round(sc[3])):
                        raise NeoOnnxError(f"scales {sc}: the same factor on H and W")
                elif sizes is not None:
                    L.attrs = dict(sizes=[int(v) for v in sizes])
                else:
                    raise NeoOnnxError("neither scales nor sizes")
                L.kind = "resize"
            elif op == "MatMul":
                A, B = n.input
                if is_const(B):
                    w = const(B).astype(np.float64)
                    if w.ndim != 2:
                        raise NeoOnnxError(f"a constant operand of {w.ndim} dimensions: a weight matrix [K, N]")
                    L.kind = "linear"
                    L.params["w"] = w
                    out = n.output[0]
                    nxt = sole_consumer(out, "Add")
                    if nxt is not None:
                        other = [x for x in nxt.input if x != out]
                        bc = const(other[0]) if other and is_const(other[0]) else None
                        if bc is not None and bc.size == w.shape[1]:
                            L.params["b"] = bc.astype(np.float64).reshape(-1)
                            absorb(nxt, L, nxt.output[0])
                            out = nxt.output[0]
                    gelu_fusion(L, out, consumers, outputs, absorb, sole_consumer, const, is_const)
                    act_fusion(L, L.outputs[0])
                elif is_const(A):
                    raise NeoOnnxError("the constant operand must be the second (X W)")
                else:
                    L.kind = "matmul"                                              # two activations (attention)
                    out = n.output[0]
                    nxt = sole_consumer(out)
                    if nxt is not None and nxt.op_type in ("Div", "Mul"):         # the scalar scale of Q K^T
                        other = [x for x in nxt.input if x != out]
                        sc = const(other[0]) if other and is_const(other[0]) else None
                        if sc is not None and sc.size == 1:
                            v = float(sc.reshape(-1)[0])
                            L.attrs["scale"] = (1.0 / v) if nxt.op_type == "Div" else v
                            absorb(nxt, L, nxt.output[0])
            elif op == "Gemm":
                if a.get("alpha", 1.0) != 1.0 or a.get("beta", 1.0) != 1.0 or a.get("transA", 0):
                    raise NeoOnnxError("alpha = beta = 1 and no transA")
                w = const(n.input[1])
                if w is None:
                    raise NeoOnnxError("the weights are not constant")
                w = w.astype(np.float64)
                if a.get("transB", 0):
                    w = w.T
                L.kind = "linear"
                L.params["w"] = w
                if len(n.input) > 2 and n.input[2]:
                    L.params["b"] = const(n.input[2]).astype(np.float64).reshape(-1)
                gelu_fusion(L, n.output[0], consumers, outputs, absorb, sole_consumer, const, is_const)
                act_fusion(L, L.outputs[0])
            elif op == "Softmax":
                L.kind = "softmax"
                L.attrs = dict(axis=int(a.get("axis", -1)))
            elif op == "LayerNormalization":
                if int(a.get("axis", -1)) not in (-1,):
                    raise NeoOnnxError(f"axis {a.get('axis')}: the last axis only")
                L.kind = "layernorm"
                L.attrs = dict(eps=float(a.get("epsilon", 1e-5)))
                L.params["g"] = const(n.input[1]).astype(np.float64).reshape(-1)
                if len(n.input) > 2 and n.input[2]:
                    L.params["b"] = const(n.input[2]).astype(np.float64).reshape(-1)
            elif op == "Gelu":
                L.kind = "gelu"
            elif op == "Add":
                A, B = n.input
                if is_const(A) or is_const(B):
                    c = const(A) if is_const(A) else const(B)
                    x = B if is_const(A) else A
                    L.inputs = [x]
                    sm = sole_consumer(n.output[0], "Softmax")
                    if sm is not None and c.ndim >= 2:                              # an additive mask before a softmax
                        L.kind = "softmax"
                        L.attrs = dict(axis=int(_attrs(sm).get("axis", -1)))
                        L.params["mask"] = c.astype(np.float64)
                        absorb(sm, L, sm.output[0])
                    else:
                        L.kind = "addc"
                        L.params["c"] = c.astype(np.float64)
                else:
                    L.kind = "add"
            elif op == "Transpose":
                L.kind = "transpose"
                L.attrs = dict(perm=[int(v) for v in a["perm"]])
            elif op == "Reshape":
                shp = const(n.input[1])
                if shp is None:
                    raise NeoOnnxError("the shape is not constant")
                L.kind = "reshape"
                L.attrs = dict(shape=[int(v) for v in shp])
            elif op == "Flatten":
                L.kind = "flatten"
                L.attrs = dict(axis=int(a.get("axis", 1)))
            elif op == "Identity":
                L.kind = "identity"
            elif op == "Relu":
                L.kind = "relu"
            elif op == "Sigmoid":
                L.kind = "sigmoid"
            elif op == "Unsqueeze":
                axes = const(n.input[1]) if len(n.input) > 1 else a.get("axes")
                L.kind = "unsqueeze"
                L.attrs = dict(axes=[int(v) for v in axes])
            elif op == "Squeeze":
                axes = const(n.input[1]) if len(n.input) > 1 and n.input[1] else a.get("axes")
                L.kind = "squeeze"
                L.attrs = dict(axes=[int(v) for v in axes] if axes is not None else None)
            elif op == "Pow":
                # the RMS-norm form: Pow(x, 2) -> ReduceMean(-1) -> Add(eps) -> Sqrt -> Div(x, .) -> Mul(g)
                e = const(n.input[1])
                rm = sole_consumer(n.output[0], "ReduceMean")
                if e is None or float(e.reshape(-1)[0]) != 2.0 or rm is None:
                    raise NeoOnnxError("Pow outside the RMS-norm form (x^2 -> ReduceMean -> Add eps -> Sqrt -> Div -> Mul gain)")
                ad = sole_consumer(rm.output[0], "Add")
                sq = sole_consumer(ad.output[0], "Sqrt") if ad is not None else None
                dv = sole_consumer(sq.output[0], "Div") if sq is not None else None
                if dv is None or dv.input[0] != n.input[0]:
                    raise NeoOnnxError("Pow outside the RMS-norm form")
                mu = sole_consumer(dv.output[0], "Mul")
                eps = const([x for x in ad.input if x != rm.output[0]][0])
                L.kind = "rmsnorm"
                L.inputs = [n.input[0]]
                L.attrs = dict(eps=float(eps.reshape(-1)[0]))
                for m in (rm, ad, sq, dv):
                    absorb(m, L, m.output[0])
                if mu is not None:
                    gname = [x for x in mu.input if x != dv.output[0]][0]
                    if is_const(gname):
                        L.params["g"] = const(gname).astype(np.float64).reshape(-1)
                        absorb(mu, L, mu.output[0])
                if "g" not in L.params:
                    L.params["g"] = None
            elif op in ("Mul", "Div", "Sub", "ReduceMean", "Sqrt", "Erf"):
                A = n.input[0]
                B = n.input[1] if len(n.input) > 1 else None
                if op in ("Mul", "Div") and B is not None and (is_const(B) and const(B).size == 1):
                    v = float(const(B).reshape(-1)[0])
                    L.kind = "scale"
                    L.inputs = [A]
                    L.attrs = dict(scale=(1.0 / v) if op == "Div" else v)
                elif op == "Mul" and B is not None and is_const(B):
                    L.kind = "mulc"
                    L.inputs = [A]
                    L.params["c"] = const(B).astype(np.float64)
                else:
                    raise NeoOnnxError(f"{op} outside a fused form (SiLU, GELU, the RMS norm, a scalar scale)")
            else:
                raise NeoOnnxError("operator not supported by the importer")
        except NeoOnnxError as e:
            fail(n, str(e))
            continue
        graph.layers.append(L)
    infer_shapes(graph)
    return graph


def gelu_fusion(L, out, consumers, outputs, absorb, sole_consumer, const, is_const):
    """The Erf form of GELU after `out`: Div(x, sqrt 2) -> Erf -> Add(1) -> Mul(x, .) -> Mul(0.5) (the Mul by 0.5 may come
    before or after the Mul by x); or the Gelu operator itself."""
    cs = consumers.get(out, [])
    if out in outputs:
        return
    g = next((c for c in cs if c.op_type == "Gelu"), None)
    if g is not None and len(cs) == 1:
        L.attrs["act"] = "gelu"
        absorb(g, L, g.output[0])
        return
    dv = next((c for c in cs if c.op_type == "Div"), None)
    if dv is None or len(cs) != 2:
        return
    other = [x for x in dv.input if x != out]
    k = const(other[0]) if other and is_const(other[0]) else None
    if k is None or abs(float(k.reshape(-1)[0]) - math.sqrt(2.0)) > 1e-4:
        return
    erf = sole_consumer(dv.output[0], "Erf")
    ad = sole_consumer(erf.output[0], "Add") if erf is not None else None
    if ad is None:
        return
    m1 = sole_consumer(ad.output[0], "Mul")
    if m1 is None:
        return
    mx = next((c for c in cs if c.op_type == "Mul"), None)
    chain = [dv, erf, ad]
    if m1 is mx:                                                           # x * (1 + erf) then * 0.5
        chain.append(m1)
        m2 = sole_consumer(m1.output[0], "Mul")
        if m2 is None:
            return
        chain.append(m2)
        last = m2
    else:                                                                  # (1 + erf) * 0.5 then * x
        chain.append(m1)
        m2 = sole_consumer(m1.output[0], "Mul")
        if m2 is None or m2 is not mx:
            return
        chain.append(m2)
        last = m2
    L.attrs["act"] = "gelu"
    for m in chain:
        absorb(m, L, m.output[0])
    L.outputs = [last.output[0]]


# ---------------------------------------------------------------------------------------------------------------------
# the float executor (the reference)
# ---------------------------------------------------------------------------------------------------------------------
def conv2d(x, w, b, k, s, p):
    """x [cin, h, w], w [cout, cin, k, k] -> [cout, ho, wo] (float64, the direct sum)."""
    cin, h, ww = x.shape
    cout = w.shape[0]
    xp = np.pad(x, ((0, 0), (p, p), (p, p)))
    ho, wo = (h + 2 * p - k) // s + 1, (ww + 2 * p - k) // s + 1
    cols = np.empty((ho * wo, cin * k * k), dtype=np.float64)
    for oy in range(ho):
        for ox in range(wo):
            cols[oy * wo + ox] = xp[:, oy * s: oy * s + k, ox * s: ox * s + k].reshape(-1)
    y = cols @ w.reshape(cout, -1).T
    if b is not None:
        y = y + b[None, :]
    return y.T.reshape(cout, ho, wo)


def maxpool2d(x, k, s, p):
    cin, h, ww = x.shape
    xp = np.pad(x, ((0, 0), (p, p), (p, p)), constant_values=-np.inf)
    ho, wo = (h + 2 * p - k) // s + 1, (ww + 2 * p - k) // s + 1
    y = np.empty((cin, ho, wo), dtype=x.dtype)
    for oy in range(ho):
        for ox in range(wo):
            y[:, oy, ox] = xp[:, oy * s: oy * s + k, ox * s: ox * s + k].reshape(cin, -1).max(axis=1)
    return y


def gelu_f(y):
    erf = np.vectorize(math.erf)
    return 0.5 * y * (1.0 + erf(y / math.sqrt(2.0)))


def apply_act(kind, y):
    """The fused activation of a conv / linear layer in float."""
    if kind is None:
        return y
    if kind == "relu":
        return np.maximum(y, 0.0)
    if kind == "silu":
        return y / (1.0 + np.exp(-y))
    if kind == "gelu":
        return gelu_f(y)
    raise NeoOnnxError(f"activation {kind}")


def run_float(graph: Graph, feeds: Dict[str, np.ndarray], keep_all=True, keep_pre=False) -> Dict[str, np.ndarray]:
    """Execute the graph in float64; returns every tensor (keep_all) or the outputs. keep_pre: the pre-activation of a
    conv / linear layer with a fused activation as '<layer>:pre' too (the quantizer's table ranges)."""
    t = {k: np.asarray(v, dtype=np.float64) for k, v in feeds.items()}
    for L in graph.layers:
        a = L.attrs
        xs = [t[n] for n in L.inputs]
        if L.kind == "conv":
            x = xs[0]
            assert x.ndim == 4 and x.shape[0] == 1, f"{L.name}: a [1, C, H, W] input"
            y = conv2d(x[0], L.params["w"], L.params.get("b"), a["k"], a["s"], a["p"])[None]
            if keep_pre and a.get("act"):
                t[L.name + ":pre"] = y
            y = apply_act(a.get("act"), y)
        elif L.kind == "maxpool":
            y = maxpool2d(xs[0][0], a["k"], a["s"], a["p"])[None]
        elif L.kind == "concat":
            y = np.concatenate(xs, axis=a["axis"])
        elif L.kind == "split":
            ax = a["axis"]
            n = xs[0].shape[ax]
            sizes = a["split"] or [n // a["num_outputs"]] * a["num_outputs"]
            parts = np.split(xs[0], np.cumsum(sizes)[:-1], axis=ax)
            for name, part in zip(L.outputs, parts):
                t[name] = part
            continue
        elif L.kind == "resize":
            x = xs[0]
            if "scale" in a:
                f = a["scale"]
                y = x.repeat(f, axis=-2).repeat(f, axis=-1)
            else:
                sz = a["sizes"]
                fy, fx = sz[-2] // x.shape[-2], sz[-1] // x.shape[-1]
                y = x.repeat(fy, axis=-2).repeat(fx, axis=-1)
        elif L.kind == "linear":
            y = xs[0] @ L.params["w"]
            if "b" in L.params:
                y = y + L.params["b"]
            if keep_pre and a.get("act"):
                t[L.name + ":pre"] = y
            y = apply_act(a.get("act"), y)
        elif L.kind == "matmul":
            y = xs[0] @ xs[1]
            if "scale" in a:
                y = y * a["scale"]
        elif L.kind == "softmax":
            y = xs[0]
            if "mask" in L.params:
                y = y + L.params["mask"]
            ax = a["axis"]
            y = y - y.max(axis=ax, keepdims=True)
            y = np.exp(y)
            y = y / y.sum(axis=ax, keepdims=True)
        elif L.kind == "layernorm":
            x = xs[0]
            mu = x.mean(axis=-1, keepdims=True)
            var = ((x - mu) ** 2).mean(axis=-1, keepdims=True)
            y = (x - mu) / np.sqrt(var + a["eps"]) * L.params["g"]
            if "b" in L.params:
                y = y + L.params["b"]
        elif L.kind == "rmsnorm":
            x = xs[0]
            y = x / np.sqrt((x * x).mean(axis=-1, keepdims=True) + a["eps"])
            if L.params.get("g") is not None:
                y = y * L.params["g"]
        elif L.kind == "gelu":
            y = gelu_f(xs[0])
        elif L.kind == "relu":
            y = np.maximum(xs[0], 0.0)
        elif L.kind == "sigmoid":
            y = 1.0 / (1.0 + np.exp(-xs[0]))
        elif L.kind == "add":
            y = xs[0] + xs[1]
        elif L.kind == "addc":
            y = xs[0] + L.params["c"]
        elif L.kind == "mulc":
            y = xs[0] * L.params["c"]
        elif L.kind == "scale":
            y = xs[0] * a["scale"]
        elif L.kind == "transpose":
            y = xs[0].transpose(a["perm"])
        elif L.kind == "reshape":
            shp = list(a["shape"])
            for i, v in enumerate(shp):
                if v == 0:
                    shp[i] = xs[0].shape[i]
            y = xs[0].reshape(shp)
        elif L.kind == "flatten":
            x = xs[0]
            y = x.reshape(int(np.prod(x.shape[:a["axis"]])) if a["axis"] else 1, -1)
        elif L.kind == "identity":
            y = xs[0]
        elif L.kind == "unsqueeze":
            y = xs[0]
            for ax in sorted(a["axes"]):
                y = np.expand_dims(y, ax)
        elif L.kind == "squeeze":
            y = np.squeeze(xs[0], axis=tuple(a["axes"]) if a["axes"] else None)
        else:
            raise NeoOnnxError(f"{L.name}: kind {L.kind} has no float executor")
        t[L.outputs[0]] = y
    if keep_all:
        return t
    return {n: t[n] for n in graph.outputs}


def infer_shapes(graph: Graph):
    """Every tensor's shape from an execution on zeros; onnx.shape_inference's answer, where it gives one, must agree."""
    feeds = {n: np.zeros(s) for n, s in graph.inputs.items()}
    with np.errstate(all="ignore"):                                       # a norm of zeros is NaN; only the shapes matter
        t = run_float(graph, feeds)
    graph.shapes = {n: tuple(int(v) for v in np.shape(x)) for n, x in t.items()}
    for L in graph.layers:
        L.in_shapes = [graph.shapes[n] for n in L.inputs]
        L.out_shapes = [graph.shapes[n] for n in L.outputs]
    return graph.shapes


def check_against_onnx_shapes(graph: Graph, model) -> List[str]:
    """onnx.shape_inference's static shapes against the executed ones; the disagreements (an empty list = agreement)."""
    import onnx
    model = load_model(model)
    try:
        inf = onnx.shape_inference.infer_shapes(model)
    except Exception as e:  # noqa: BLE001
        return [f"onnx.shape_inference failed: {e}"]
    bad = []
    for vi in list(inf.graph.value_info) + list(inf.graph.output):
        dims = vi.type.tensor_type.shape.dim
        if not dims or any(not d.HasField("dim_value") for d in dims):
            continue
        shp = tuple(d.dim_value for d in dims)
        if vi.name in graph.shapes and graph.shapes[vi.name] != shp:
            bad.append(f"{vi.name}: executed {graph.shapes[vi.name]}, onnx {shp}")
    return bad


# ---------------------------------------------------------------------------------------------------------------------
# mapping onto the compiler
# ---------------------------------------------------------------------------------------------------------------------
def conv_layers(graph: Graph):
    """neo_compile.Conv shapes of the graph's convolutions (the perf models' and the static compiler's input)."""
    import neo_compile as nc
    out = []
    for L in graph.layers:
        if L.kind != "conv":
            continue
        _, cin, h, w = L.in_shapes[0]
        cout = L.params["w"].shape[0]
        out.append(nc.Conv(L.name, h, w, cin, cout, L.attrs["k"], L.attrs["s"], L.attrs["p"]))
    return out


def block_from_graph(graph: Graph):
    """Recognise a transformer block (one head) in the imported graph and return (BlockCfg, W, g1, g2, x_name): the form
    neo_xfmr.block_plan compiles -- norm1 (layer norm or RMS norm with a gain) on x, Q / K / V linear layers on it, S = Q K^T
    scaled by 1 / sqrt(d) (a causal additive mask optional), softmax, P V, the output projection, the residual x +, norm2,
    W1 with GELU or SiLU, W2, the residual +. Biases are not part of the block form (an error names the layer). The weights
    come back as the model stores them (float32 values in float64 arrays): a plan compiled from them equals block_plan on
    the same float32-rounded weights bit for bit."""
    import neo_xfmr as nx
    kinds = [L.kind for L in graph.layers]
    norms = [L for L in graph.layers if L.kind in ("layernorm", "rmsnorm")]
    if len(norms) != 2:
        raise NeoOnnxError(f"a block has two norms, the graph has {len(norms)}")
    n1, n2 = norms
    if n1.kind != n2.kind:
        raise NeoOnnxError("the two norms differ in kind")
    rms = n1.kind == "rmsnorm"
    x_name = n1.inputs[0]
    lin = [L for L in graph.layers if L.kind == "linear"]
    for L in lin:
        if "b" in L.params:
            raise NeoOnnxError(f"layer '{L.name}' has a bias: the block form has none")
    qkv = [L for L in lin if L.inputs[0] == n1.outputs[0]]
    if len(qkv) != 3:
        raise NeoOnnxError(f"three projections of norm1's output expected, {len(qkv)} found")
    mm = [L for L in graph.layers if L.kind == "matmul"]
    if len(mm) != 2:
        raise NeoOnnxError(f"two activation MatMuls (Q K^T, P V) expected, {len(mm)} found")
    s_mm = next((L for L in mm if "scale" in L.attrs), None)
    pv = next((L for L in mm if L is not s_mm), None)
    if s_mm is None:
        raise NeoOnnxError("Q K^T without its 1 / sqrt(d) scale")

    def source(name):
        """The linear layer a tensor comes from through transposes / reshapes."""
        L = graph.layer_of(name)
        while L is not None and L.kind in ("transpose", "reshape", "identity", "unsqueeze", "squeeze"):
            L = graph.layer_of(L.inputs[0])
        return L
    q_l, k_l = source(s_mm.inputs[0]), source(s_mm.inputs[1])
    v_l = source(pv.inputs[1])
    if not (q_l in qkv and k_l in qkv and v_l in qkv and len({id(q_l), id(k_l), id(v_l)}) == 3):
        raise NeoOnnxError("Q, K and V are not the three projections (Q K^T's operands, P V's second)")
    sm = next((L for L in graph.layers if L.kind == "softmax"), None)
    if sm is None or source(pv.inputs[0]) is not None and graph.layer_of(pv.inputs[0]) is not sm:
        raise NeoOnnxError("P V's first operand is not the softmax")
    d = q_l.params["w"].shape[1]
    if abs(s_mm.attrs["scale"] * math.sqrt(d) - 1.0) > 1e-6:
        raise NeoOnnxError(f"the score scale {s_mm.attrs['scale']} is not 1 / sqrt({d})")
    causal = False
    if "mask" in sm.params:
        m = sm.params["mask"]
        m2 = m.reshape(m.shape[-2], m.shape[-1])
        T = m2.shape[0]
        want = np.triu(np.full((T, T), -1e30), 1)
        if m2.shape[0] != m2.shape[1] or not np.array_equal(m2 <= -1e9, want <= -1e9) or np.any(m2[np.tril_indices(T)] != 0):
            raise NeoOnnxError("the softmax mask is not the causal (strictly upper triangular) mask")
        causal = True
    o_l = next((L for L in lin if source(L.inputs[0]) is pv), None)
    if o_l is None:
        raise NeoOnnxError("no output projection of P V")
    add1 = next((L for L in graph.layers if L.kind == "add" and set(L.inputs) == {x_name, o_l.outputs[0]}), None)
    if add1 is None:
        raise NeoOnnxError("no residual x + (P V) Wo")
    if n2.inputs[0] != add1.outputs[0]:
        raise NeoOnnxError("norm2 does not take the first residual's output")
    w1 = next((L for L in lin if L.inputs[0] == n2.outputs[0]), None)
    if w1 is None or w1.attrs.get("act") not in ("gelu", "silu"):
        raise NeoOnnxError("W1 with GELU or SiLU after norm2 not found")
    w2 = next((L for L in lin if L.inputs[0] == w1.outputs[0]), None)
    if w2 is None:
        raise NeoOnnxError("W2 after the activation not found")
    add2 = next((L for L in graph.layers if L.kind == "add" and set(L.inputs) == {add1.outputs[0], w2.outputs[0]}), None)
    if add2 is None or add2.outputs[0] not in graph.outputs:
        raise NeoOnnxError("the block's output is not h + W2(...)")
    T, D = graph.shapes[x_name][-2], graph.shapes[x_name][-1]
    H = w1.params["w"].shape[1]
    cfg = nx.BlockCfg(name=graph.name, T=T, D=D, d=d, H=H, rms=rms, act=w1.attrs["act"], int4=False, causal=causal)
    W = dict(q=q_l.params["w"], k=k_l.params["w"], v=v_l.params["w"], o=o_l.params["w"], w1=w1.params["w"], w2=w2.params["w"])
    g1 = n1.params["g"] if n1.params.get("g") is not None else np.ones(D)
    g2 = n2.params["g"] if n2.params.get("g") is not None else np.ones(D)
    return cfg, W, g1, g2, x_name


# ---------------------------------------------------------------------------------------------------------------------
# model builders for the tests (small models, one per operator family; nothing is committed)
# ---------------------------------------------------------------------------------------------------------------------
def _model(nodes, inputs, outputs, inits, name="neo_test"):
    import onnx
    from onnx import helper, numpy_helper
    graph = helper.make_graph(nodes, name, inputs, outputs, initializer=[numpy_helper.from_array(np.asarray(v) if np.asarray(v).dtype.kind in "iu" else np.asarray(v, dtype=np.float32), n)
                                                                  for n, v in inits.items()])
    opsets = [helper.make_opsetid("", OPSET_MAX)]
    if any(n.domain for n in nodes):                                      # the Gelu operator of onnxruntime's contrib domain
        opsets += [helper.make_opsetid(d, 1) for d in sorted({n.domain for n in nodes if n.domain})]
    model = helper.make_model(graph, opset_imports=opsets, producer_name="neo_onnx tests")
    model.ir_version = 8
    onnx.checker.check_model(model)
    return model


def _vi(name, shape):
    from onnx import helper, TensorProto
    return helper.make_tensor_value_info(name, TensorProto.FLOAT, list(shape))


def _const_node(name, value):
    from onnx import helper, numpy_helper
    return helper.make_node("Constant", [], [name], name="c_" + name, value=numpy_helper.from_array(np.asarray(value, dtype=np.float32)))


def build_conv_model(rng, cin=3, cout=8, h=8, w=8, k=3, s=1, act="silu", bias=True, bias_as_add=False):
    """Conv [+ bias] [+ Relu | SiLU]: the convolution family."""
    from onnx import helper
    p = k // 2
    W = rng.normal(0, 1.0 / math.sqrt(cin * k * k), (cout, cin, k, k))
    inits = {"W": W}
    nodes, out = [], "y0"
    conv_in = ["x", "W"]
    if bias and not bias_as_add:
        inits["B"] = rng.normal(0, 0.1, cout)
        conv_in.append("B")
    nodes.append(helper.make_node("Conv", conv_in, [out], name="conv0", kernel_shape=[k, k], strides=[s, s], pads=[p, p, p, p]))
    if bias and bias_as_add:
        inits["B4"] = rng.normal(0, 0.1, (1, cout, 1, 1))
        nodes.append(helper.make_node("Add", [out, "B4"], ["y1"], name="bias_add"))
        out = "y1"
    if act == "relu":
        nodes.append(helper.make_node("Relu", [out], ["y"], name="relu0"))
    elif act == "silu":
        nodes.append(helper.make_node("Sigmoid", [out], ["sg"], name="sig0"))
        nodes.append(helper.make_node("Mul", [out, "sg"], ["y"], name="silu_mul"))
    else:
        nodes.append(helper.make_node("Identity", [out], ["y"], name="id0"))
    ho, wo = (h + 2 * p - k) // s + 1, (w + 2 * p - k) // s + 1
    return _model(nodes, [_vi("x", (1, cin, h, w))], [_vi("y", (1, cout, ho, wo))], inits, "conv_family")


def build_pool_concat_split_resize_model(rng, c=8, h=8, w=8):
    """MaxPool, Split, Resize (x2 nearest), Concat: the layout family."""
    from onnx import helper
    nodes = [helper.make_node("MaxPool", ["x"], ["p"], name="pool0", kernel_shape=[3, 3], strides=[2, 2], pads=[1, 1, 1, 1]),
             helper.make_node("Split", ["p", "sp"], ["a", "b"], name="split0", axis=1),
             helper.make_node("Resize", ["a", "", "sc"], ["u"], name="resize0", mode="nearest", nearest_mode="floor",
                              coordinate_transformation_mode="asymmetric"),
             helper.make_node("Resize", ["b", "", "sc"], ["v"], name="resize1", mode="nearest", nearest_mode="floor",
                              coordinate_transformation_mode="asymmetric"),
             helper.make_node("Concat", ["u", "v", "x"], ["y"], name="concat0", axis=1)]
    inits = {"sp": np.array([c // 2, c // 2], dtype=np.int64), "sc": np.array([1, 1, 2, 2], dtype=np.float32)}
    return _model(nodes, [_vi("x", (1, c, h, w))], [_vi("y", (1, 2 * c, h, w))], inits, "layout_family")


def build_linear_model(rng, T=8, D=16, H=32, act="gelu", gemm=False):
    """MatMul + Add bias + GELU (Erf form) then Gemm: the linear family."""
    from onnx import helper
    W1, b1, W2, b2 = rng.normal(0, 1 / math.sqrt(D), (D, H)), rng.normal(0, 0.1, H), rng.normal(0, 1 / math.sqrt(H), (H, D)), rng.normal(0, 0.1, D)
    inits = {"W1": W1, "b1": b1, "W2": W2 if not gemm else W2.T, "b2": b2}
    nodes = [helper.make_node("MatMul", ["x", "W1"], ["h0"], name="mm1"), helper.make_node("Add", ["h0", "b1"], ["h1"], name="add_b1")]
    if act == "gelu":
        nodes += [_const_node("sqrt2", math.sqrt(2.0)), _const_node("one", 1.0), _const_node("half", 0.5),
                  helper.make_node("Div", ["h1", "sqrt2"], ["g0"], name="gelu_div"), helper.make_node("Erf", ["g0"], ["g1"], name="gelu_erf"),
                  helper.make_node("Add", ["g1", "one"], ["g2"], name="gelu_add"), helper.make_node("Mul", ["h1", "g2"], ["g3"], name="gelu_mulx"),
                  helper.make_node("Mul", ["g3", "half"], ["z"], name="gelu_half")]
    elif act == "gelu_op":
        nodes.append(helper.make_node("Gelu", ["h1"], ["z"], name="gelu0", domain="com.microsoft"))   # opset 17 has no Gelu: the contrib operator
    else:
        nodes += [helper.make_node("Sigmoid", ["h1"], ["sg"], name="sig"), helper.make_node("Mul", ["sg", "h1"], ["z"], name="silu")]
    if gemm:
        nodes.append(helper.make_node("Gemm", ["z", "W2", "b2"], ["y"], name="gemm2", transB=1))
    else:
        nodes += [helper.make_node("MatMul", ["z", "W2"], ["y0"], name="mm2"), helper.make_node("Add", ["y0", "b2"], ["y"], name="add_b2")]
    return _model(nodes, [_vi("x", (T, D))], [_vi("y", (T, D))], inits, "linear_family")


def build_attention_model(rng, T=8, D=16, d=16, causal=False):
    """Softmax, Transpose, activation MatMuls, the scale and the mask: the attention family (Q, K, V linear)."""
    from onnx import helper
    Wq, Wk, Wv = (rng.normal(0, 1 / math.sqrt(D), (D, d)) for _ in range(3))
    inits = {"Wq": Wq, "Wk": Wk, "Wv": Wv}
    nodes = [helper.make_node("MatMul", ["x", "Wq"], ["q"], name="mq"), helper.make_node("MatMul", ["x", "Wk"], ["k"], name="mk"),
             helper.make_node("MatMul", ["x", "Wv"], ["v"], name="mv"), helper.make_node("Transpose", ["k"], ["kt"], name="tk", perm=[1, 0]),
             helper.make_node("MatMul", ["q", "kt"], ["s0"], name="qk"), _const_node("sqd", math.sqrt(d)),
             helper.make_node("Div", ["s0", "sqd"], ["s1"], name="scale")]
    s = "s1"
    if causal:
        inits["mask"] = np.triu(np.full((T, T), -1e30), 1)
        nodes.append(helper.make_node("Add", ["s1", "mask"], ["s2"], name="mask_add"))
        s = "s2"
    nodes += [helper.make_node("Softmax", [s], ["p"], name="softmax0", axis=-1), helper.make_node("MatMul", ["p", "v"], ["y"], name="pv")]
    return _model(nodes, [_vi("x", (T, D))], [_vi("y", (T, d))], inits, "attention_family")


def build_norm_model(rng, T=8, D=16, rms=False):
    """LayerNormalization (opset 17) or the RMS-norm form, then Reshape / Flatten: the normalisation family."""
    from onnx import helper
    g = rng.uniform(0.5, 1.5, D)
    inits = {"g": g, "shp": np.array([1, T, D], dtype=np.int64)}
    if rms:
        nodes = [_const_node("two", 2.0), _const_node("eps", 1e-6),
                 helper.make_node("Pow", ["x", "two"], ["x2"], name="pow"), helper.make_node("ReduceMean", ["x2"], ["ms"], name="mean", axes=[-1], keepdims=1),
                 helper.make_node("Add", ["ms", "eps"], ["ms1"], name="eps_add"), helper.make_node("Sqrt", ["ms1"], ["r"], name="sqrt"),
                 helper.make_node("Div", ["x", "r"], ["n0"], name="div"), helper.make_node("Mul", ["n0", "g"], ["n"], name="gain")]
    else:
        inits["b"] = rng.normal(0, 0.1, D)
        nodes = [helper.make_node("LayerNormalization", ["x", "g", "b"], ["n"], name="ln0", axis=-1, epsilon=1e-5)]
    nodes += [helper.make_node("Reshape", ["n", "shp"], ["r2"], name="reshape0"), helper.make_node("Flatten", ["r2"], ["y"], name="flat0", axis=1)]
    return _model(nodes, [_vi("x", (T, D))], [_vi("y", (1, T * D))], inits, "norm_family")


def build_block_model(cfg, W, g1, g2):
    """A transformer block as neo_xfmr.block_float computes it (norm1, attention, residual, norm2, MLP, residual), in ONNX:
    the model the importer's block path re-compiles for M23 (layer norm, GELU) and M24 (RMS norm, causal, SiLU)."""
    from onnx import helper
    T, D, d, H = cfg.T, cfg.D, cfg.d, cfg.H
    inits = {"g1": g1, "g2": g2, "Wq": W["q"], "Wk": W["k"], "Wv": W["v"], "Wo": W["o"], "W1": W["w1"], "W2": W["w2"]}
    nodes = []

    def norm(tag, src, dst, g):
        if cfg.rms:
            nodes.extend([_const_node(f"{tag}_two", 2.0), _const_node(f"{tag}_eps", 0.0),
                          helper.make_node("Pow", [src, f"{tag}_two"], [f"{tag}_x2"], name=f"{tag}_pow"),
                          helper.make_node("ReduceMean", [f"{tag}_x2"], [f"{tag}_ms"], name=f"{tag}_mean", axes=[-1], keepdims=1),
                          helper.make_node("Add", [f"{tag}_ms", f"{tag}_eps"], [f"{tag}_ms1"], name=f"{tag}_eps_add"),
                          helper.make_node("Sqrt", [f"{tag}_ms1"], [f"{tag}_r"], name=f"{tag}_sqrt"),
                          helper.make_node("Div", [src, f"{tag}_r"], [f"{tag}_n0"], name=f"{tag}_div"),
                          helper.make_node("Mul", [f"{tag}_n0", g], [dst], name=f"{tag}_gain")])
        else:
            nodes.append(helper.make_node("LayerNormalization", [src, g], [dst], name=tag, axis=-1, epsilon=0.0))
    norm("norm1", "x", "n1", "g1")
    nodes += [helper.make_node("MatMul", ["n1", "Wq"], ["q"], name="q"), helper.make_node("MatMul", ["n1", "Wk"], ["k"], name="k"),
              helper.make_node("MatMul", ["n1", "Wv"], ["v"], name="v"), helper.make_node("Transpose", ["k"], ["kt"], name="kt", perm=[1, 0]),
              helper.make_node("MatMul", ["q", "kt"], ["s0"], name="qk"), _const_node("sqd", math.sqrt(d)),
              helper.make_node("Div", ["s0", "sqd"], ["s1"], name="scale")]
    s = "s1"
    if cfg.causal:
        inits["mask"] = np.triu(np.full((T, T), -1e30), 1)
        nodes.append(helper.make_node("Add", ["s1", "mask"], ["s2"], name="mask"))
        s = "s2"
    nodes += [helper.make_node("Softmax", [s], ["p"], name="softmax", axis=-1), helper.make_node("MatMul", ["p", "v"], ["a"], name="pv"),
              helper.make_node("MatMul", ["a", "Wo"], ["ao"], name="o"), helper.make_node("Add", ["x", "ao"], ["h"], name="res1")]
    norm("norm2", "h", "n2", "g2")
    nodes.append(helper.make_node("MatMul", ["n2", "W1"], ["z0"], name="w1"))
    if cfg.act == "gelu":
        nodes += [_const_node("sqrt2", math.sqrt(2.0)), _const_node("one", 1.0), _const_node("half", 0.5),
                  helper.make_node("Div", ["z0", "sqrt2"], ["g0"], name="gelu_div"), helper.make_node("Erf", ["g0"], ["g1e"], name="gelu_erf"),
                  helper.make_node("Add", ["g1e", "one"], ["g2e"], name="gelu_add"), helper.make_node("Mul", ["z0", "g2e"], ["g3"], name="gelu_mulx"),
                  helper.make_node("Mul", ["g3", "half"], ["z"], name="gelu_half")]
    else:
        nodes += [helper.make_node("Sigmoid", ["z0"], ["sg"], name="sig"), helper.make_node("Mul", ["z0", "sg"], ["z"], name="silu")]
    nodes += [helper.make_node("MatMul", ["z", "W2"], ["z2"], name="w2"), helper.make_node("Add", ["h", "z2"], ["y"], name="res2")]
    return _model(nodes, [_vi("x", (T, D))], [_vi("y", (T, D))], inits, cfg.name.replace(" ", "_"))


def build_yolov8m_group(rng, h=12, w=12, nblk=2, c_in=3):
    """The first conv stack of YOLOv8-m (b0: 3 -> 48 k3 s2, b1: 48 -> 96 k3 s2) and its first C2f block (b2.c2f: cv1 96 -> 96
    1x1, Split, two Bottlenecks of 48 -> 48 3x3 convs with residuals, Concat of the four 48-channel maps, cv2 192 -> 96 1x1),
    every conv with a bias (the folded batch norm) and SiLU; the published widths at a spatial crop h x w of the 640^2 input
    (the weights random: the released checkpoints are not in the repository)."""
    from onnx import helper
    inits, nodes = {"b2.split": np.array([48, 48], dtype=np.int64)}, []

    def conv(tag, src, dst, cin, cout, k, s):
        p = k // 2
        inits[f"{tag}.w"] = rng.normal(0, 1.0 / math.sqrt(cin * k * k), (cout, cin, k, k))
        inits[f"{tag}.b"] = rng.normal(0, 0.05, cout)
        nodes.extend([helper.make_node("Conv", [src, f"{tag}.w", f"{tag}.b"], [f"{tag}.c"], name=f"{tag}.conv", kernel_shape=[k, k],
                                       strides=[s, s], pads=[p, p, p, p]),
                      helper.make_node("Sigmoid", [f"{tag}.c"], [f"{tag}.s"], name=f"{tag}.sig"),
                      helper.make_node("Mul", [f"{tag}.c", f"{tag}.s"], [dst], name=f"{tag}.silu")])
    conv("b0", "x", "b0.y", c_in, 48, 3, 2)
    conv("b1", "b0.y", "b1.y", 48, 96, 3, 2)
    c = 48
    conv("b2.cv1", "b1.y", "b2.cv1.y", 96, 2 * c, 1, 1)
    nodes.append(helper.make_node("Split", ["b2.cv1.y", "b2.split"], ["b2.y0", "b2.y1"], name="b2.split", axis=1))
    cat = ["b2.y0", "b2.y1"]
    prev = "b2.y1"
    for i in range(nblk):
        conv(f"b2.m{i}.cv1", prev, f"b2.m{i}.cv1.y", c, c, 3, 1)
        conv(f"b2.m{i}.cv2", f"b2.m{i}.cv1.y", f"b2.m{i}.cv2.y", c, c, 3, 1)
        nodes.append(helper.make_node("Add", [prev, f"b2.m{i}.cv2.y"], [f"b2.m{i}.y"], name=f"b2.m{i}.add"))
        prev = f"b2.m{i}.y"
        cat.append(prev)
    nodes.append(helper.make_node("Concat", cat, ["b2.cat"], name="b2.concat", axis=1))
    conv("b2.cv2", "b2.cat", "y", (2 + nblk) * c, 96, 1, 1)
    ho = (h + 2 - 3) // 2 + 1
    ho2 = (ho + 2 - 3) // 2 + 1
    wo = (w + 2 - 3) // 2 + 1
    wo2 = (wo + 2 - 3) // 2 + 1
    return _model(nodes, [_vi("x", (1, c_in, h, w))], [_vi("y", (1, 96, ho2, wo2))], inits, "yolov8m_group")


def ort_run(model, feeds):
    """onnxruntime's outputs of a model (float32)."""
    import onnxruntime as ort
    sess = ort.InferenceSession(model.SerializeToString(), providers=["CPUExecutionProvider"])
    return sess.run(None, {k: np.asarray(v, dtype=np.float32) for k, v in feeds.items()})


def main():
    import argparse
    ap = argparse.ArgumentParser(description="import an ONNX model and print its layer table")
    ap.add_argument("model")
    ap.add_argument("--lenient", action="store_true", help="report unsupported operators instead of failing")
    a = ap.parse_args()
    g = import_model(a.model, strict=not a.lenient)
    print(f"{g.name}: opset {g.opset}, inputs {g.inputs}, outputs {g.outputs}, {len(g.layers)} layers")
    print(g.table())
    for name, op, why in g.unsupported:
        print(f"UNSUPPORTED node '{name}' ({op}): {why}")
    return 1 if g.unsupported else 0


if __name__ == "__main__":
    sys.exit(main())
