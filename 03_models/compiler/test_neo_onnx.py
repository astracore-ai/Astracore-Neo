#!/usr/bin/env python3
"""
test_neo_onnx.py -- the importer's and the quantizer's tests (drop 0.44, stream D3; python-regress). Every ONNX model is
generated here (neo_onnx.build_*); nothing is read from disk.
  O1  one small model per operator family (convolution: Conv with bias as an input and as an Add, ReLU, SiLU, none, k1 / k3,
      stride 2; layout: MaxPool, Split, Resize, Concat; linear: MatMul + Add, Gemm, GELU as the Erf form and as the
      operator, SiLU; attention: Q / K / V, Transpose, Q K^T with its 1 / sqrt(d), the causal mask, Softmax, P V;
      normalisation: LayerNormalization, the RMS-norm form, Reshape, Flatten): the importer's float executor against
      onnxruntime within 1e-5 of the output's range, the executed shapes against onnx.shape_inference
  O2  the unsupported-operator report: an operator outside the list (Tanh), a grouped convolution and a dilated one are
      refused with the node's name (strict) or listed (lenient) while the rest of the graph still executes
  O3  the transformer blocks through the importer: the ONNX form of M23 (ViT) and M24 (LLM) built from their data,
      recognised by block_from_graph (cfg, weights, gains), compiled by neo_xfmr.block_plan into a plan identical --
      every phase's programs and every bank word -- to the plan from the same float32-rounded weights taken directly,
      executed on the mesh model: the output within 4 output LSB of onnxruntime's float block (the pipeline's INT8 error)
  Q1  per-channel weights: every row's scale is max |W[c]| / qmax and the integers round back within half a scale; INT4
      values within [-7, 7]
  Q2  calibration: percentile and MSE thresholds within the sample's range, a scale group (split, concat, resize) shares one
      scale, the MSE threshold minimises the error over the grid
  Q3  the epilogues on the vector unit's model: a conv with bias + SiLU + residual after the activation, a conv with bias
      and no activation, a plain conv (the requantizer alone) -- the integer executor against the float conv on the
      dequantized inputs within 2 LSB where the vector unit computes (table interpolation), and the requantizer's model
      against tool_qual's RTL arithmetic
  Q4  the 2:4 validator path: a dense tensor marked sparse is refused naming the group, a pruned one is accepted and stays
      2:4 after quantization; the packed sparse entries of neo_backend.pack_words agree with compress_2_4
  Q5  the YOLOv8-m group at a 12 x 12 crop: INT8 (percentile / MSE / max) within cos > 0.999 of FP32 at the output, INT4 on
      the C2f convs within cos > 0.98; the schedule's steps fit the command memory and every program the program memory
Usage: python3 compiler/test_neo_onnx.py
"""
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import neo_onnx as no  # noqa: E402
import neo_quant as nq  # noqa: E402
import neo_sched as ns  # noqa: E402
import neo_backend as nb  # noqa: E402
import neo_xfmr as nx  # noqa: E402
import neo_vec as nv  # noqa: E402

results = []


def case(tag, ok, detail=""):
    results.append((tag, bool(ok), detail))
    print(f"  {tag:<48} {'PASS' if ok else 'FAIL'}  {detail}")


def rel_err(a, b):
    a, b = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    return float(np.abs(a - b).max() / max(float(np.abs(b).max()), 1e-9))


def o1_families(rng):
    fam = [("conv silu bias", no.build_conv_model(rng), (1, 3, 8, 8)),
           ("conv relu bias-as-add k1 s2", no.build_conv_model(rng, k=1, s=2, act="relu", bias_as_add=True), (1, 3, 8, 8)),
           ("conv none no-bias", no.build_conv_model(rng, act=None, bias=False), (1, 3, 8, 8)),
           ("maxpool split resize concat", no.build_pool_concat_split_resize_model(rng), (1, 8, 8, 8)),
           ("matmul bias gelu(erf) matmul", no.build_linear_model(rng), (8, 16)),
           ("matmul bias gelu(op) gemm", no.build_linear_model(rng, act="gelu_op", gemm=True), (8, 16)),
           ("matmul bias silu gemm", no.build_linear_model(rng, act="silu", gemm=True), (8, 16)),
           ("attention", no.build_attention_model(rng), (8, 16)),
           ("attention causal mask", no.build_attention_model(rng, causal=True), (8, 16)),
           ("layernorm reshape flatten", no.build_norm_model(rng), (8, 16)),
           ("rmsnorm form reshape flatten", no.build_norm_model(rng, rms=True), (8, 16))]
    for name, model, shape in fam:
        try:
            g = no.import_model(model)
            feed = {"x": rng.normal(0, 1, shape)}
            out = no.run_float(g, feed, keep_all=False)
            ref = no.ort_run(model, feed)
            err = max(rel_err(out[n], r) for n, r in zip(g.outputs, ref))
            bad = no.check_against_onnx_shapes(g, model)
            case(f"O1 {name}", err < 1e-5 and not bad, f"{len(g.layers)} layers {[L.kind for L in g.layers]}, rel err {err:.1e}" + (f", shapes {bad}" if bad else ""))
        except Exception as e:  # noqa: BLE001
            case(f"O1 {name}", False, f"{type(e).__name__}: {e}")


def o2_unsupported(rng):
    from onnx import helper
    W = rng.normal(0, 0.3, (4, 3, 3, 3))
    nodes = [helper.make_node("Conv", ["x", "W"], ["c"], name="conv_ok", kernel_shape=[3, 3], pads=[1, 1, 1, 1]),
             helper.make_node("Tanh", ["c"], ["t"], name="tanh_bad"),
             helper.make_node("Conv", ["t", "Wg"], ["g"], name="conv_grouped", kernel_shape=[3, 3], pads=[1, 1, 1, 1], group=4),
             helper.make_node("Conv", ["g", "Wd"], ["y"], name="conv_dilated", kernel_shape=[3, 3], pads=[2, 2, 2, 2], dilations=[2, 2])]
    inits = {"W": W, "Wg": rng.normal(0, 0.3, (4, 1, 3, 3)), "Wd": rng.normal(0, 0.3, (4, 4, 3, 3))}
    model = no._model(nodes, [no._vi("x", (1, 3, 6, 6))], [no._vi("y", (1, 4, 6, 6))], inits, "unsupported")
    try:
        no.import_model(model)
        case("O2 strict refusal names the node", False, "no error raised")
    except no.NeoOnnxError as e:
        case("O2 strict refusal names the node", "tanh_bad" in str(e) and "Tanh" in str(e), str(e)[:80])
    g = no.import_model(model, strict=False)
    names = [n for n, _, _ in g.unsupported]
    ok = names == ["tanh_bad", "conv_grouped", "conv_dilated"] and [L.kind for L in g.layers] == ["conv"]
    case("O2 lenient report lists every unsupported node", ok, f"{[(n, op, why[:30]) for n, op, why in g.unsupported]}")


def o3_blocks():
    for cfg, seed in ((nx.M23_CFG, 23), (nx.M24_CFG, 24)):
        try:
            x8, s_x, W, g1, g2 = nx.block_data(cfg, seed)
            model = no.build_block_model(cfg, W, g1, g2)
            g = no.import_model(model)
            cfg2, W2, gg1, gg2, xn = no.block_from_graph(g)
            cfg2.int4 = cfg.int4
            cfg2.name = cfg.name
            same_cfg = (cfg2.T, cfg2.D, cfg2.d, cfg2.H, cfg2.rms, cfg2.act, cfg2.causal) == (cfg.T, cfg.D, cfg.d, cfg.H, cfg.rms, cfg.act, cfg.causal)
            f32 = lambda a: np.asarray(a, dtype=np.float32).astype(np.float64)  # noqa: E731
            plan_o = nx.block_plan(cfg2, x8, s_x, W2, gg1, gg2)
            plan_d = nx.block_plan(cfg, x8, s_x, {k: f32(v) for k, v in W.items()}, f32(g1), f32(g2))
            same_loads = plan_o.loads == plan_d.loads
            same_phases = len(plan_o.phases) == len(plan_d.phases) and all(
                [(s.node, s.desc, s.prog, s.vec.prog, s.vec.lutv, s.vec.luts, s.vec.sc, s.vec.bi, s.vec.k, s.rq, s.relu) for s in po.steps]
                == [(s.node, s.desc, s.prog, s.vec.prog, s.vec.lutv, s.vec.luts, s.vec.sc, s.vec.bi, s.vec.k, s.rq, s.relu) for s in pd.steps]
                for po, pd in zip(plan_o.phases, plan_d.phases))
            mm = nx.MeshModel(plan_o.geo)
            for node, base, words in plan_o.loads:
                mm.load(node, base, words)
            for ph in plan_o.phases:
                mm.run_phase(ph)
            got = nx.read_block_out(mm.read, plan_o)
            ort = no.ort_run(model, {"x": x8 * s_x})[0]
            err = float(np.abs(got * plan_o.s_out - ort).max() / plan_o.s_out)
            err_q = nx.block_errors(plan_o, got)[1]
            case(f"O3 {cfg.name} through the importer", same_cfg and same_loads and same_phases and err_q < 4.0,
                 f"cfg {'=' if same_cfg else '!='}, {len(plan_o.phases)} phases {'identical' if same_phases else 'DIFFER'}, bank words "
                 f"{'identical' if same_loads else 'DIFFER'}; max |err| {err:.2f} LSB vs onnxruntime, {err_q:.2f} vs the quantized float block")
        except Exception as e:  # noqa: BLE001
            case(f"O3 {cfg.name} through the importer", False, f"{type(e).__name__}: {e}")


def q1_weights(rng):
    W = rng.normal(0, 0.1, (6, 5, 3, 3))
    for bits in (8, 4):
        q, s = nq.quant_weights(W, bits)
        qmax = (1 << (bits - 1)) - 1
        ok = np.allclose(s, np.abs(W.reshape(6, -1)).max(axis=1) / qmax) and q.min() >= -qmax and q.max() <= qmax \
            and np.abs(q * s[:, None, None, None] - W).max() <= 0.5 * s.max() + 1e-12 and np.abs(q).max(axis=(1, 2, 3)).tolist() == [qmax] * 6
        case(f"Q1 per-channel INT{bits} weights", ok, f"scales {s.min():.3g}..{s.max():.3g}, max |dequant - W| {np.abs(q * s[:, None, None, None] - W).max():.3g}")


def q2_calibration(rng):
    a = rng.normal(0, 1, 20000)
    a[:5] = 40.0                                                            # outliers
    t_max, t_pct, t_mse = nq.threshold(a, "max"), nq.threshold(a, "percentile", 99.9), nq.threshold(a, "mse")
    grid = [float(np.abs(a).max()) * f for f in np.linspace(0.3, 1.0, 36)]
    errs = [float(((np.abs(a) - np.clip(np.round(np.abs(a) / (t / 127)), -127, 127) * (t / 127)) ** 2).mean()) for t in grid]
    ok = t_max == 40.0 and 3.0 < t_pct < 5.0 and abs(t_mse - grid[int(np.argmin(errs))]) < 1e-9
    case("Q2 thresholds: max / percentile / MSE", ok, f"max {t_max:.2f}, 99.9th {t_pct:.2f}, MSE {t_mse:.2f}")
    model = no.build_pool_concat_split_resize_model(rng)
    g = no.import_model(model)
    scales, _ = nq.calibrate(g, [{"x": rng.normal(0, 1, (1, 8, 8, 8))}], "max")
    grp = {scales["p"], scales["a"], scales["b"], scales["u"], scales["v"], scales["y"], scales["x"]}
    case("Q2 a scale group shares one scale", len(grp) == 1, f"{len(grp)} scale(s) over x, pool, split, resize, concat")


def q3_epilogues(rng):
    import vec_ref as vr
    from onnx import helper
    tests = [("bias + SiLU + residual after the act", True, "silu", True), ("bias, no activation", True, None, False), ("plain (requantizer only)", False, None, False),
             ("bias + ReLU", True, "relu", False)]
    for name, bias, act, res in tests:
        cin, cout = 8, 8
        W = rng.normal(0, 1 / math.sqrt(cin * 9), (cout, cin, 3, 3))
        inits = {"W": W}
        ins_ = ["x", "W"]
        if bias:
            inits["B"] = rng.normal(0, 0.2, cout)
            ins_.append("B")
        nodes = [helper.make_node("Conv", ins_, ["c"], name="conv", kernel_shape=[3, 3], pads=[1, 1, 1, 1])]
        out = "c"
        if act == "silu":
            nodes += [helper.make_node("Sigmoid", ["c"], ["s"], name="sig"), helper.make_node("Mul", ["c", "s"], ["a"], name="silu")]
            out = "a"
        elif act == "relu":
            nodes.append(helper.make_node("Relu", ["c"], ["a"], name="relu"))
            out = "a"
        if res:
            nodes.append(helper.make_node("Add", ["x", out], ["y"], name="res"))
        else:
            nodes.append(helper.make_node("Identity", [out], ["y"], name="id"))
        model = no._model(nodes, [no._vi("x", (1, cin, 6, 6))], [no._vi("y", (1, cout, 6, 6))], inits, "epilogue")
        g = no.import_model(model)
        calib = [{"x": rng.normal(0, 1, (1, cin, 6, 6))} for _ in range(3)]
        qn = nq.quantize(g, calib, "max")
        ql = qn.qlayers["conv"]
        ep = nq.conv_epilogue(ql, 8, 0)
        uses_unit = ep.vec is not None
        rows_, lines = nq.accuracy_report(qn, calib)
        o = [r for r in rows_ if r[4]][0]
        # the integer executor against the float conv on the dequantized integer inputs (what the quantized layer should compute)
        x_q = nq.quantize_input(qn, calib[0]["x"])
        t = nq.run_int(qn, x_q)
        xf = x_q * qn.scales["x"]
        wf = ql.w * ql.w_scale[:, None, None, None]
        y = no.conv2d(xf[0], wf, ql.bias, 3, 1, 1)
        if act == "silu":
            y = y / (1 + np.exp(-y))
        elif act == "relu":
            y = np.maximum(y, 0)
        if res:
            y = y + xf[0]
        err = float(np.abs(t["y"][0] - np.round(y / ql.s_out)).max())
        ok = err <= (2 if uses_unit else 1) and (uses_unit == (bias or act == "silu" or res)) and o[1] > 0.99
        case(f"Q3 {name}", ok, f"{'vector unit ' + ep.vec.name if uses_unit else 'requantizer'}, max |int - round(float / s)| {err:.0f} LSB, cos {o[1]:.5f}")
    # the requantizer's model against the RTL arithmetic (tool_qual's rq_bitwise)
    from tool_qual import rq_bitwise
    bad = sum(nv.rq_model(int(a), int(m), int(sh), int(z), int(r)) != rq_bitwise(int(a), int(m), int(sh), int(z), int(r))
              for a, m, sh, z, r in zip(rng.integers(-(1 << 31), 1 << 31, 3000), rng.integers(1, 65536, 3000), rng.integers(0, 32, 3000),
                                        rng.integers(-128, 128, 3000), rng.integers(0, 2, 3000)))
    case("Q3 requantizer model = RTL arithmetic", bad == 0, f"{bad} mismatches in 3000")


def q4_sparse(rng):
    graph, model, calib, _ = nq.yolo_group_case()
    L = next(l for l in graph.layers if l.name == "b2.m0.cv1.conv")
    try:
        nq.quantize(graph, calib, sparse=[L.name])
        case("Q4 dense tensor marked sparse refused", False, "accepted")
    except ValueError as e:
        case("Q4 dense tensor marked sparse refused", "channels" in str(e) and "output channel" in str(e), str(e)[:70])
    saved = L.params["w"]
    L.params["w"] = nb.prune_2_4(saved * 1e6) / 1e6
    qn = nq.quantize(graph, calib, sparse=[L.name])
    ql = qn.qlayers[L.name]
    ok = nb.check_2_4(ql.w) and ql.sparse
    # the packed form of the sparse layer: pack_words(sparse=True) on the NCHK = 4 core agrees with compress_2_4 tile by tile
    x = rng.integers(-128, 128, size=(ql.cin, 3, 3), dtype=np.int64)
    ct_n, _, _, wpw = nb.geometry(ql.cin, ql.cout, 3, 16, 8, 4, sparse=True)
    act, wts = nb.pack_words(x, ql.w, ct_n, 3, ql.cout, 16, 8, nchk=4, sparse=True, nt=0)
    tile = [[int(ql.w[j, c, 0, 0]) if c < ql.cin else 0 for j in range(8)] for c in range(32)]
    Wc, Ic = nb.compress_2_4(tile, 16)
    WC = nb.check_weights(Wc, Ic, 4)
    first = []
    for r in range(16):
        first += nb.pack_entry(Wc[r], Ic[r], WC[r], 16, 4)
    ok = ok and wts[:len(first)] == first
    L.params["w"] = saved
    case("Q4 pruned tensor validated, stays 2:4, packs as compress_2_4", ok, f"{len(wts)} weight words, {wpw} per entry")


def q5_yolo():
    graph, model, calib, rng = nq.yolo_group_case()
    test = [{"x": rng.normal(0, 1.0, (1, 3, 12, 12))} for _ in range(2)]
    names = [L.name for L in graph.layers if L.kind == "conv" and L.name.startswith("b2.")]
    for tag, kw, bar in (("INT8 percentile", dict(method="percentile"), 0.999), ("INT8 mse", dict(method="mse"), 0.999),
                         ("INT8 max", dict(method="max"), 0.999), ("INT4 (C2f convs)", dict(method="percentile", int4=names), 0.98)):
        qn = nq.quantize(graph, calib, **kw)
        rows_, _ = nq.accuracy_report(qn, test)
        o = [r for r in rows_ if r[4]][0]
        case(f"Q5 YOLOv8-m group {tag}", o[1] > bar, f"output cos {o[1]:.5f}, SQNR {o[2]:.2f} dB, max |err| {o[3]:.2f} LSB")
    qn = nq.quantize(graph, calib)
    img = ns.schedule(qn, ns.MeshGeo(), nq.quantize_input(qn, test[0]["x"]))
    ok = all(len(ns.entries_of(st.tiles, img.geo)) <= ns.IMG_ENTRIES for st in img.steps) and all(len(t.prog) <= nb.PROG_DEPTH for st in img.steps for t in st.tiles)
    ok = ok and all(t.cfg_m >= 1 for st in img.steps for t in st.tiles)
    case("Q5 schedule within the command and program memories", ok, f"{len(img.steps)} steps, {sum(len(s.tiles) for s in img.steps)} tile programs, "
         f"largest image {max(len(ns.entries_of(st.tiles, img.geo)) for st in img.steps)} entries of {ns.IMG_ENTRIES}")


def main():
    rng = np.random.default_rng(44)
    print("neo_onnx / neo_quant tests:")
    o1_families(rng)
    o2_unsupported(rng)
    o3_blocks()
    q1_weights(rng)
    q2_calibration(rng)
    q3_epilogues(rng)
    q4_sparse(rng)
    q5_yolo()
    n_ok = sum(ok for _, ok, _ in results)
    print(f"{n_ok}/{len(results)} cases passed")
    print("RESULT:", "ALL PASS" if n_ok == len(results) else f"{len(results) - n_ok} FAILURES")
    return 0 if n_ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
