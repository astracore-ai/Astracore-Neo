#!/usr/bin/env python3
"""
neo_vec.py -- the compiler's side of the vector unit (drop 0.43, stream C2): the LUTs, the per-layer vector programs of the
transformer operators and their constants, emitted as the host writes that load them (host_if's VEC_ADDR / VEC_DATA).

The unit (rtl/vec_unit.sv, model/vec_ref.py) runs a program of up to 16 micro-ops on each drained row of a layer. A row
holds COLS output channels; the operators whose reduction spans more channels than a row (a softmax over keys in several
key tiles, a layer norm over channels in several output tiles) run the tiles of a row block on one core in rounds and
combine the per-tile lane reductions in the row statistics RS0 / RS1, which persist across descriptors:

  softmax over keys (S = Q K^T: rows = queries, lanes = keys of tile kb)
    round 1, every key tile      RED max -> RS0 (store on the first tile, max after)            [prog_softmax_max]
    round 2, every key tile      e = exp(scale * (s - RS0)) (LUTV exp, Q15); EMIT e; RS1 (+)= sum of e   [prog_softmax_exp]
    P V, every output tile        O = (E V) * (1 / RS1): the reciprocal (LUTS, offset domain) and one Newton step in the
                                  scalars, BMUL                                                [prog_softmax_norm]
  layer norm / RMS norm over channels (rows = tokens, lanes = channels of output tile nt)
    round 1, every output tile    [ADD_ROW residual] [EMIT] x_s = x * q (K0); RS0 (+)= sum x_s; RS1 (+)= sum x_s^2 >> sh;
                                  the last tile turns the sums into mean and variance          [prog_norm_stats]
    round 2, every output tile    (recomputed) [ADD_ROW] x_s; (x_s - mean) * rsqrt(var) (LUTS); MUL_SCALE gamma, beta
                                  (per column, the next layer's input scale folded in); EMIT     [prog_norm_apply]
  GELU / SiLU                     x * q >> sh (K0) -> LUTV; EMIT                               [prog_activation]
  residual add                    ADD_ROW (the INT8 residual shifted to the accumulator's scale) [prog_residual]

Tables: 256 entries {D[31:16], V[15:0]} over the LUT's INT16 input u, x = c + u * s_in, V = round(f(x) / s_out) (vec_ref.
build_lut). The fixed formats below give the V2 bounds of sim/run_vec_unit.py:
  exp        x in [-8, 0]   (s_in 2^-12)              out Q15   (entry 0 is 0: an input at or below -8 -- exp < 2^-11,
                                                                  zero after an INT8 requant -- adds nothing to a sum)
  GELU, SiLU x in [-8, 8]   (s_in 2^-12)              out Q12
  1/x        x in [1, 64]   (c 32.5, s_in 2^-10)      out Q15   (the softmax denominator in units of its largest term)
  1/sqrt(x)  x in [1/8, 8]  (c 4.0625, s_in 2^-13)    out Q13   (the variance in units of the layer's calibrated
                                                                  variance: the compiler scales it into this range)
"""
import math
import os
import sys
from dataclasses import dataclass, field
from typing import List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "model"))
import vec_ref as vr  # noqa: E402
from vec_ref import vop  # noqa: E402

EXP_S_IN, EXP_S_OUT = 2.0 ** -12, 2.0 ** -15
ACT_S_IN, ACT_S_OUT = 2.0 ** -12, 2.0 ** -12
RCP_C, RCP_S_IN, RCP_S_OUT, RCP_LO, RCP_HI = 32.5, 2.0 ** -10, 2.0 ** -15, 1.0, 64.0
RSQ_C, RSQ_S_IN, RSQ_S_OUT, RSQ_LO, RSQ_HI = 4.0625, 2.0 ** -13, 2.0 ** -13, 0.125, 8.0
Q15 = 1 << 15

REG_VEC_ADDR, REG_VEC_DATA = 0x38, 0x39          # host_if's vector-unit window (drop 0.43)


def gelu(x):
    return 0.5 * x * (1.0 + math.erf(x / math.sqrt(2.0)))


def silu(x):
    return x / (1.0 + math.exp(-x)) if x > -60 else 0.0


def lut_exp(s_in=EXP_S_IN, s_out=EXP_S_OUT):
    """exp(x) for x <= 0; entry 0 (x = -8, where every input below the domain saturates) holds 0 instead of exp(-8) = 11
    LSB, so a key far below the row's maximum adds nothing to the softmax denominator (64 such keys would add 2 %)."""
    lo = -32768 * s_in
    return vr.build_lut(lambda x: 0.0 if x <= lo + 1e-12 else math.exp(min(x, 0.0)), s_in, s_out, vmin=0)


def lut_gelu(s_in=ACT_S_IN, s_out=ACT_S_OUT):
    return vr.build_lut(gelu, s_in, s_out)


def lut_silu(s_in=ACT_S_IN, s_out=ACT_S_OUT):
    return vr.build_lut(silu, s_in, s_out)


def lut_recip(s_in=RCP_S_IN, s_out=RCP_S_OUT, c=RCP_C):
    """1/x on x = c + u * s_in (x >= 1 on the domain's low end; values below 1 clip at 1)."""
    return vr.build_lut(lambda x: 1.0 / max(x + c, RCP_LO), s_in, s_out, vmin=0)


def lut_rsqrt(s_in=RSQ_S_IN, s_out=RSQ_S_OUT, c=RSQ_C):
    """1/sqrt(x) on x = c + u * s_in (clipped below at the range's low end)."""
    return vr.build_lut(lambda x: 1.0 / math.sqrt(max(x + c, RSQ_LO)), s_in, s_out, vmin=0)


# ---------------------------------------------------------------------------------------------------------------------
# the reciprocal of the softmax denominator, in the unit's scalars (prog_softmax_norm's first seven ops) -- also the model
# V2 measures
# ---------------------------------------------------------------------------------------------------------------------
RCP_K_OFF = int(round(RCP_C * Q15))             # K0: c in the denominator's units (1.0 = 2^15, an exp's Q15 maximum)
RCP_K_TWO = 2 * (1 << 14)                       # K1: 2.0 in Q14
RCP_SH_U = 5                                    # u = (L - K0) >> 5: x = L / 2^15 at s_in = 2^-10


def recip_newton_int(L, luts):
    """r ~ 2^15 / L (Q15) for a denominator L (the sum of Q15 exps, L >= 2^15): u = sat16((L - c 2^15) >> 5) indexes the
    1/x table, then r1 = r0 (2 - L r0 / 2^16 ...) -- the Newton step in the unit's fixed point (t in Q14)."""
    u = vr.s16((vr.sat(L - RCP_K_OFF)) >> RCP_SH_U)
    r0 = vr.lut_eval(luts, u)
    t = vr.sat(vr.rshr(L * vr.s16(r0), 16))
    dd = vr.sat(RCP_K_TWO - t)
    return vr.sat(vr.rshr(r0 * vr.s16(dd), 14))


_RCP_TABLE = None


def recip_newton_model(x):
    """V2: 1/x through the table and the Newton step, for x in [1, 64] (the denominator in units of its largest term)."""
    global _RCP_TABLE
    if _RCP_TABLE is None:
        _RCP_TABLE = lut_recip()
    return recip_newton_int(int(round(x * Q15)), _RCP_TABLE) / Q15


# ---------------------------------------------------------------------------------------------------------------------
# a layer's vector configuration and its host writes
# ---------------------------------------------------------------------------------------------------------------------
@dataclass
class VecConfig:
    """The program and tables of one descriptor (None: a table the layer does not use -- it keeps its contents)."""
    prog: List[int]
    lutv: Optional[List[int]] = None
    luts: Optional[List[int]] = None
    sc: Optional[List[int]] = None
    bi: Optional[List[int]] = None
    k: Optional[List[int]] = None
    name: str = ""

    def table_writes(self, skip=()):
        """(address, data) of every table this configuration sets, the program last (its slot 0 activates the unit).
        skip: table ids already loaded with the same contents (the caller tracks them per tile)."""
        w = []
        for tbl, vals in ((vr.T_LUTV, self.lutv), (vr.T_LUTS, self.luts), (vr.T_SC, self.sc), (vr.T_BI, self.bi),
                          (vr.T_K, self.k)):
            if vals is not None and tbl not in skip:
                w += vr.table_writes(tbl, vals)
        return w + vr.program_writes(self.prog)

    def apply(self, vu, skip=()):
        vu.load(self.table_writes(skip))


PASS = VecConfig([vop(vr.OP_END)], name="pass")      # slot 0 END: the bypass


def host_writes(writes):
    """The register writes that load (address, data) pairs through VEC_ADDR / VEC_DATA: VEC_ADDR once per run of
    consecutive addresses (VEC_DATA increments it)."""
    regs, nxt = [], None
    for a, d in writes:
        if a != nxt:
            regs.append((REG_VEC_ADDR, a))
        regs.append((REG_VEC_DATA, d & 0xFFFFFFFF))
        nxt = a + 1
    return regs


# ---------------------------------------------------------------------------------------------------------------------
# programs
# ---------------------------------------------------------------------------------------------------------------------
def prog_softmax_max(first):
    """Round 1 of a softmax across key tiles: RS0 = max over this tile's keys (stored on the first tile, max after)."""
    return [vop(vr.OP_RED, d=0, mode=0), vop(vr.OP_RSST, a=0, t=0, mode=0 if first else 1)]


def prog_softmax_exp(first, sh_scale):
    """Round 2: e = exp((s - max) * K0 >> sh_scale) in Q15 (the LUT's input scale 2^-12), EMIT e (the requantizer makes it
    INT8 for P V), RS1 = sum of e (stored on the first tile, added after). K0 = the score scale in the exp table's units."""
    return [vop(vr.OP_SLD, d=0, mode=0, t=0), vop(vr.OP_BSUB, a=0), vop(vr.OP_BMUL, a=0, ka=1, sh=sh_scale),
            vop(vr.OP_LUT, sh=0), vop(vr.OP_EMIT), vop(vr.OP_RED, d=2, mode=1),
            vop(vr.OP_RSST, a=2, t=1, mode=0 if first else 2)]


def prog_softmax_norm(sh_out):
    """P V's drain: r = 1/RS1 (Q15: u = (L - K0) >> 5 into the 1/x table, then a Newton step with K1 = 2.0 in Q14),
    O = round(O' * r >> sh_out), EMIT. K0 = RCP_K_OFF, K1 = RCP_K_TWO."""
    return [vop(vr.OP_SLD, d=0, mode=0, t=1),                                  # S0 = L
            vop(vr.OP_SALU, d=3, a=0, b=0, kb=1, mode=1),                      # S3 = L - K0
            vop(vr.OP_SLUT, d=2, a=3, sh=RCP_SH_U),                            # S2 = r0 (Q15)
            vop(vr.OP_SMUL, d=3, a=0, b=2, sh=16),                             # S3 = t = L r0 >> 16 (Q14)
            vop(vr.OP_SALU, d=3, a=1, ka=1, b=3, mode=1),                      # S3 = 2 - t (K1)
            vop(vr.OP_SMUL, d=2, a=2, b=3, sh=14),                             # S2 = r1 (Q15)
            vop(vr.OP_BMUL, a=2, sh=sh_out), vop(vr.OP_EMIT)]


def prog_norm_stats(first, last, inv_d_k=1, sh_x=0, sh_sq=0, residual_sh=None, emit=False, rms=False):
    """Round 1 of a layer norm (rms: an RMS norm) over channels in several output tiles: [ADD_ROW] [EMIT the row] x_s =
    round(x * K0 >> sh_x); RS0 (+)= sum x_s (not for rms); RS1 (+)= sum round(x_s^2 >> sh_sq); on the last tile mean =
    RS0 * K1 >> 15 (K1 = 2^15 / D) -> RS0 and var = RS1 * K1 >> 15 - mean^2 >> sh_sq -> RS1 (rms: the mean square)."""
    p = []
    if residual_sh is not None:
        p.append(vop(vr.OP_ADD_ROW, sh=residual_sh))
    if emit:
        p.append(vop(vr.OP_EMIT))
    p.append(vop(vr.OP_BMUL, a=0, ka=1, sh=sh_x))
    if not rms:
        p += [vop(vr.OP_RED, d=0, mode=1), vop(vr.OP_RSST, a=0, t=0, mode=0 if first else 2)]
    p += [vop(vr.OP_SQR, sh=sh_sq), vop(vr.OP_RED, d=1, mode=1), vop(vr.OP_RSST, a=1, t=1, mode=0 if first else 2)]
    if last:
        p += [vop(vr.OP_SLD, d=1, mode=0, t=1), vop(vr.OP_SMUL, d=1, a=1, b=inv_d_k, kb=1, sh=15)]
        if not rms:
            p += [vop(vr.OP_SLD, d=0, mode=0, t=0), vop(vr.OP_SMUL, d=0, a=0, b=inv_d_k, kb=1, sh=15),
                  vop(vr.OP_SMUL, d=2, a=0, b=0, sh=sh_sq), vop(vr.OP_SALU, d=1, a=1, b=2, mode=1),
                  vop(vr.OP_RSST, a=0, t=0, mode=0)]
        p.append(vop(vr.OP_RSST, a=1, t=1, mode=0))
    assert len(p) <= 16, f"layer-norm statistics program of {len(p)} ops"
    return p


def prog_norm_apply(off_k, sh_x=0, sh_u=0, sh_r=0, sh_g=0, residual_sh=None, rms=False):
    """Round 2: [ADD_ROW] x_s = x * K0 >> sh_x; r = rsqrt-table(sat16((var - K[off_k]) >> sh_u)) (the variance in the
    table's domain); (x_s - mean) * r >> sh_r (rms: x_s * r); MUL_SCALE gamma, beta (>> sh_g; the next layer's input scale
    folded in); EMIT."""
    p = []
    if residual_sh is not None:
        p.append(vop(vr.OP_ADD_ROW, sh=residual_sh))
    p.append(vop(vr.OP_BMUL, a=0, ka=1, sh=sh_x))
    if not rms:
        p += [vop(vr.OP_SLD, d=0, mode=0, t=0), vop(vr.OP_BSUB, a=0)]
    p += [vop(vr.OP_SLD, d=1, mode=0, t=1), vop(vr.OP_SALU, d=1, a=1, b=off_k, kb=1, mode=1),
          vop(vr.OP_SLUT, d=1, a=1, sh=sh_u), vop(vr.OP_BMUL, a=1, sh=sh_r), vop(vr.OP_MUL_SCALE, sh=sh_g), vop(vr.OP_EMIT)]
    return p


def prog_activation(sh_in, residual_sh=None):
    """[ADD_ROW] x * K0 >> sh_in into the activation table's input scale (2^-12), LUTV (GELU / SiLU, Q12), EMIT."""
    p = []
    if residual_sh is not None:
        p.append(vop(vr.OP_ADD_ROW, sh=residual_sh))
    return p + [vop(vr.OP_BMUL, a=0, ka=1, sh=sh_in), vop(vr.OP_LUT, sh=0), vop(vr.OP_EMIT)]


def prog_norm_row(sh_a, sh_r, sh_sq=0):
    """A layer norm over the row's own COLS lanes (all of a layer's output channels in one output tile -- the 32x32 core's
    32), both passes on the held row, the output clipped: mean = sum x * K0 >> 15 (K0 = 2^15 / COLS); V = sum (x - mean)^2
    >> sh_sq; r = rsqrt-table((V K1 >> sh_a) - K2) (Q13, V in units of its calibration); RELOAD; y = (x - mean) r >> sh_r;
    CLAMP to [-K3, K3] (the output's clip, ReLU6-style bounds); EMIT. 15 ops."""
    return [vop(vr.OP_RED, d=0, mode=1), vop(vr.OP_SMUL, d=0, a=0, b=0, kb=1, sh=15), vop(vr.OP_BSUB, a=0),
            vop(vr.OP_SQR, sh=sh_sq), vop(vr.OP_RED, d=2, mode=1), vop(vr.OP_SMUL, d=2, a=2, b=1, kb=1, sh=sh_a),
            vop(vr.OP_SALU, d=2, a=2, b=2, kb=1, mode=1), vop(vr.OP_SLUT, d=2, a=2, sh=0), vop(vr.OP_RELOAD),
            vop(vr.OP_BSUB, a=0), vop(vr.OP_BMUL, a=2, sh=sh_r), vop(vr.OP_SLD, d=1, mode=3),
            vop(vr.OP_SALU, d=1, a=1, b=3, kb=1, mode=1), vop(vr.OP_CLAMP, a=1, b=3, kb=1), vop(vr.OP_EMIT)]


def norm_row_epilogue(cols, v_cal, s_out, clip, sh_r=0, sh_sq=0):
    """The in-row layer norm's configuration: V calibrated at v_cal (the median of sum (x - mean)^2 >> sh_sq over rows),
    the output x-hat at s_out clipped at |x-hat| <= clip. x-hat = (x - mean) / sqrt(V / COLS) = (x - mean) r sqrt(COLS) /
    (2^13 sqrt(v_cal))."""
    k1, sh_a = fixed_mult(1.0 / (v_cal * RSQ_S_IN))
    k2 = int(round(RSQ_C / RSQ_S_IN))
    gain = math.sqrt(cols * (1 << sh_sq)) / ((1 << 13) * math.sqrt(v_cal)) * (1 << sh_r)
    k3 = int(clip / gain)                                 # the clip in y's units
    mult, shift, zp = requant_for(gain / s_out)
    vec = VecConfig(prog_norm_row(sh_a, sh_r, sh_sq), luts=lut_rsqrt(), k=[(1 << 15) // cols, k1, k2, k3], name="norm-row")
    return Epilogue(vec, [mult] * cols, [shift] * cols, [zp] * cols, None, dict(gain=gain, k3=k3))


def prog_residual(residual_sh):
    """x + RBUF[m] << residual_sh, EMIT."""
    return [vop(vr.OP_ADD_ROW, sh=residual_sh), vop(vr.OP_EMIT)]


def fixed_mult(scale, max_mult=32767):
    """(mult, shift) with mult / 2^shift ~ scale, mult < 2^15 (a sat16 operand), shift <= 31."""
    assert scale > 0
    sh = 0
    while sh < 31 and scale * (1 << (sh + 1)) <= max_mult:
        sh += 1
    m = int(round(scale * (1 << sh)))
    return max(1, min(max_mult, m)), sh


def requant_for(scale, zp=0, relu=False):
    """The requantizer's (mult, shift) for out = round(v * scale) + zp: mult < 2^16 unsigned, shift <= 31."""
    sh = 0
    while sh < 31 and scale * (1 << (sh + 1)) < 65535:
        sh += 1
    return max(0, min(65535, int(round(scale * (1 << sh))))), sh, int(zp)


@dataclass
class Epilogue:
    """A drained layer's vector program and tables, its requantizer tables (per column) and the residual's shift."""
    vec: VecConfig
    rq_mult: List[int]
    rq_shift: List[int]
    rq_zp: List[int]
    res_sh: Optional[int] = None
    notes: dict = field(default_factory=dict)


def act_epilogue(kind, s_acc, s_out, cols, s_res=None, res_sh=10, s_in_act=ACT_S_IN):
    """The epilogue of a layer whose INT32 accumulator (real value acc * s_acc) is followed by [a residual add of an INT8
    tensor at scale s_res] and an activation (kind 'silu' | 'gelu' | None), the result requantized to INT8 at s_out.
      MUL_SCALE  acc * SC >> sh1 -> z in units s_z = s_res / 2^res_sh (with a residual; else s_z = s_acc * 2^-sh_pre)
      ADD_ROW    z += r << res_sh
      BMUL K0    z * K0 >> shk -> the activation table's input (x / 2^-12)
      LUT        y in Q12 (GELU / SiLU)
      EMIT       -> requant: out = round(y * 2^-12 / s_out) + 0
    Without an activation the row goes from ADD_ROW to EMIT and the requantizer maps z to s_out."""
    prog, sc = [], None
    if s_res is not None:
        s_z = s_res / (1 << res_sh)
        m, sh1 = fixed_mult(s_acc / s_z)
        sc = [m] * cols
        prog += [vop(vr.OP_MUL_SCALE, sh=sh1), vop(vr.OP_ADD_ROW, sh=res_sh)]
    else:
        s_z, res_sh = s_acc, None
    k, lutv = [0, 0, 0, 0], None
    if kind is not None:
        k0, shk = fixed_mult(s_z / s_in_act)
        k[0] = k0
        prog += [vop(vr.OP_BMUL, a=0, ka=1, sh=shk), vop(vr.OP_LUT, sh=0)]
        lutv = lut_silu() if kind == "silu" else lut_gelu()
        s_y = ACT_S_OUT
    else:
        s_y = s_z
    prog.append(vop(vr.OP_EMIT))
    mult, shift, zp = requant_for(s_y / s_out)
    vec = VecConfig(prog, lutv=lutv, sc=sc, bi=[0] * cols if sc else None, k=k, name=f"{kind or 'linear'}"
                    + ("+residual" if s_res is not None else ""))
    return Epilogue(vec, [mult] * cols, [shift] * cols, [zp] * cols, res_sh, dict(s_z=s_z, s_y=s_y))


def rq_model(acc, mult, shift, zp, relu=False):
    """The requantizer (rtl/requant.sv): round(acc * mult >> shift) + zp, [ReLU], saturated to INT8."""
    r = (int(acc) * int(mult) + ((1 << int(shift)) >> 1)) >> int(shift)
    v = r + int(zp)
    if relu and v < zp:
        v = zp
    return max(-128, min(127, v))


def golden_epilogue(ep, rows, rbuf_rows=None, cols=None, acc_rows=64, vu=None):
    """The bit-exact result of an epilogue on accumulator rows: the vector unit's model, then the requantizer's model.
    rbuf_rows[m] = the residual row m (INT8, lanes 0..COLS-1). Returns (INT8 rows, the unit's rows, the unit)."""
    cols = cols or len(rows[0])
    if vu is None:
        vu = vr.VecUnit(cols, cols, acc_rows)
    ep.vec.apply(vu)
    if rbuf_rows is not None:
        for m, r in enumerate(rbuf_rows):
            vu.write_rbuf(m, list(r) + [0] * (cols - len(r)))
    out, _ = vu.run([list(map(int, r)) for r in rows])
    q = [[rq_model(v, ep.rq_mult[j], ep.rq_shift[j], ep.rq_zp[j]) for j, v in enumerate(r)] for r in out]
    return q, out, vu


def cycles_per_row(prog):
    """The unit's cycles per row for a program (vec_ref's count: the row's load, its ops, END)."""
    n = 1
    for w in prog:
        op = w & 15
        if op == vr.OP_END:
            break
        n += 2 if op in vr.TWO_CYCLE else 1
    return n + 1


if __name__ == "__main__":
    for name, p in (("softmax max", prog_softmax_max(False)), ("softmax exp", prog_softmax_exp(False, 15)),
                    ("softmax norm", prog_softmax_norm(15)),
                    ("norm stats (last, residual, emit)", prog_norm_stats(False, True, residual_sh=8, emit=True)),
                    ("norm apply (residual)", prog_norm_apply(2, residual_sh=8)), ("GELU", prog_activation(12))):
        print(f"{name:<36} {len(p):>2} ops, {cycles_per_row(p):>2} cycles per row: " + "; ".join(vr.disasm(w) for w in p))
