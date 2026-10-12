#!/usr/bin/env python3
"""gen_crc16_beat.py -- writes 01_rtl/crc16_beat.sv, the beat CRC in matrix form (drop 0.41).

The serial step of crc16_word (polynomial 0x1021, MSB first, a 16-bit state, 32 bits per word, no constant term: the
0xFFFF initial value is the caller's crc_in) is linear over GF(2). With A the state transform of one all-zero word
(16 x 16) and B the transform of a word into a zero state (16 x 32), the state after the first n words of a beat is

  CRC_n(c, w) = A^n c + sum_{k<n} A^(n-1-k) B w_k

and the module computes it as
  T     = the CRC over the beat padded to MAXBW words, the words at positions >= n forced to zero
        = A^MAXBW c + sum_{k<n} A^(MAXBW-1-k) B w_k = A^(MAXBW-n) CRC_n      (the zero tail multiplies the state by A per word)
  CRC_n = (A^(MAXBW-n))^-1 T                                                 (A is invertible: the polynomial has a constant term)

Every bit of T is the XOR of a fixed subset of the 16 + 32*BW input bits. The words beyond n are masked on their partial
parities, not on their bits: p_i[k] = the parity of word k under row i's 32 constants, and T_i = (crc_in part) ^ (XOR of
p_i[k] over k < n) -- the same function as zeroing the words first (a word's part of T is linear in the word), with 16 AND
gates per row instead of 512 on the inputs, and n reaching T through those 16 only. Synthesized on ASAP7 SS (unit
crc16_beat): 3,805 cells and 762 ps against 4,194 cells and 821 ps when the words themselves are masked (n's path there
went through the input mask's fanout), and 4,723 ps for the serial chain. The inverse is a 16 x 16 matrix indexed by n
itself -- no arithmetic on n in front of the table (an index BW - n cost 1,108 ps). Padding the beat to MAXBW words, rather
than inverting A^(BW-n), is what makes the index n direct and one set of constants serve every BW <= MAXBW (the tile's 16,
the testbench's 1 and 4):

  ROWi[32*k +: 32]     = row i of A^(MAXBW-1-k) B    (word k of the beat; a beat of BW words reads positions 0..BW-1)
  ROWi[32*MAXBW +: 16] = row i of A^MAXBW            (the crc_in part)
  NROWi[16*n +: 16]    = row i of (A^(MAXBW-n))^-1   for n = 0..MAXBW, by Gaussian elimination (n = MAXBW: the identity);
                         the identity for n > MAXBW, up to the 2^MW entries a CW-bit n can address

n = 0 returns crc_in exactly (T = A^MAXBW crc_in, NROW[0] its inverse); an n > BW, which no caller produces, counts the
missing words as zeros (up to MAXBW words).

The script models the serial algorithm bit by bit, builds the matrices from its responses to unit inputs, checks that the
emitted tables -- read back through a Python model of the RTL's own expressions -- reproduce the serial CRC on random beats
for several BW and every n, and writes the module. `--check` compares the generated text with the file instead of writing it
(make crc16 runs it, so the RTL in the repository is always what this script produces).
  python3 sim/gen_crc16_beat.py [--out rtl/crc16_beat.sv] [--check]
"""
import argparse
import os
import random
import sys

POLY = 0x1021
MAXBW = 16                      # the bank row: a beat is at most 16 words (neo_tile's LANES)
MW = MAXBW.bit_length()         # $clog2(MAXBW + 1): the widest n; the inverse table has 2^MW entries (32)


# ------------------------------------------------------------------ the serial algorithm (crc16_word, crc16_beat_serial)
def step_word(c, w):
    """crc16_word: the 32 bits of w, MSB first, into the 16-bit state c."""
    for b in range(31, -1, -1):
        fb = ((c >> 15) & 1) ^ ((w >> b) & 1)
        c = ((c << 1) & 0xFFFF) ^ (POLY if fb else 0)
    return c


def crc_serial(c, words, n):
    """crc16_beat_serial: the state after the first n words."""
    for k in range(n):
        c = step_word(c, words[k])
    return c


# ------------------------------------------------------------------ GF(2) matrices as lists of row bit masks
def parity(x):
    return bin(x).count("1") & 1


def mat_from_columns(cols, nrows=16):
    """cols[j] = the output vector for input bit j -> rows[i] = the input bits feeding output bit i."""
    rows = [0] * nrows
    for j, v in enumerate(cols):
        for i in range(nrows):
            if (v >> i) & 1:
                rows[i] |= 1 << j
    return rows


def mat_mul(x, y):
    """x * y for x (r x 16) and y (16 x c), rows as bit masks."""
    out = []
    for r in x:
        acc = 0
        for j in range(16):
            if (r >> j) & 1:
                acc ^= y[j]
        out.append(acc)
    return out


IDENT = [1 << i for i in range(16)]


def mat_inv(m):
    """Gauss-Jordan elimination over GF(2): the inverse of a 16 x 16 matrix (raises if singular)."""
    a = list(m)
    inv = list(IDENT)
    for col in range(16):
        piv = next((r for r in range(col, 16) if (a[r] >> col) & 1), None)
        if piv is None:
            raise ValueError("singular matrix")
        a[col], a[piv] = a[piv], a[col]
        inv[col], inv[piv] = inv[piv], inv[col]
        for r in range(16):
            if r != col and (a[r] >> col) & 1:
                a[r] ^= a[col]
                inv[r] ^= inv[col]
    return inv


def tables():
    """ROWi (16 + 32*MAXBW bits: word k at [32*k +: 32], the crc part on top) and NROWi (2^MW entries of 16 bits)."""
    A = mat_from_columns([step_word(1 << j, 0) for j in range(16)])
    B = mat_from_columns([step_word(0, 1 << b) for b in range(32)])
    apow = [list(IDENT)]
    for _ in range(MAXBW):
        apow.append(mat_mul(A, apow[-1]))
    ainv = [mat_inv(p) for p in apow]                     # (A^m)^-1 by elimination, m = 0..MAXBW
    ainv1 = mat_inv(A)                                    # cross-check: (A^m)^-1 == (A^-1)^m and A^m (A^m)^-1 == I
    q = list(IDENT)
    for m in range(MAXBW + 1):
        assert ainv[m] == q and mat_mul(apow[m], ainv[m]) == IDENT, f"inverse of A^{m}"
        q = mat_mul(ainv1, q)
    row = [apow[MAXBW][i] << (32 * MAXBW) for i in range(16)]
    for k in range(MAXBW):
        mk = mat_mul(apow[MAXBW - 1 - k], B)
        for i in range(16):
            row[i] |= mk[i] << (32 * k)
    nrow = [sum((ainv[MAXBW - n][i] if n <= MAXBW else IDENT[i]) << (16 * n) for n in range(1 << MW)) for i in range(16)]
    return row, nrow


# ------------------------------------------------------------------ the RTL's expressions, in Python, on the emitted tables
def crc_rtl_model(row, nrow, bw, c, words, n):
    cw = bw.bit_length()                                  # CW = $clog2(BW + 1)
    n &= (1 << cw) - 1
    th = sum((1 if n > k else 0) << k for k in range(bw))                     # th[k] = (n > CW'(k))
    t = 0
    for i in range(16):
        p = sum(parity((row[i] >> (32 * k)) & words[k] & 0xFFFFFFFF) << k for k in range(bw))   # pi[k]
        cpart = parity((row[i] >> (32 * MAXBW)) & 0xFFFF & c)
        t |= (cpart ^ parity(p & th)) << i                # ^({ROWi[32*MAXBW +: 16] & crc_in, pi & th})
    return sum(parity(((nrow[i] >> (16 * n)) & 0xFFFF) & t) << i for i in range(16))


def self_check(row, nrow, vectors=3000, seed=41):
    r = random.Random(seed)
    bad = 0
    for bw in (1, 2, 3, 4, 5, 8, 15, 16):
        for v in range(vectors):
            n = v % (bw + 1) if v < 4 * (bw + 1) else r.randint(0, bw)
            c = r.getrandbits(16)
            words = [r.getrandbits(32) for _ in range(bw)]
            if crc_rtl_model(row, nrow, bw, c, words, n) != crc_serial(c, words, n):
                bad += 1
        for n in range(bw + 1, 1 << bw.bit_length()):    # n > BW: the missing words count as zeros, up to MAXBW
            c = r.getrandbits(16)
            words = [r.getrandbits(32) for _ in range(bw)]
            nn = min(n, MAXBW)
            if crc_rtl_model(row, nrow, bw, c, words, n) != crc_serial(c, words + [0] * (nn - bw), nn):
                bad += 1
    return bad


# ------------------------------------------------------------------ the module text
def hexlit(width, v):
    return f"{width}'h{v:0{(width + 3) // 4}x}"


HEADER = """\
// crc16_beat.sv -- CRC-16-CCITT update over a beat of up to BW words in one cycle (drop 0.29; the matrix form, drop 0.41).
//   GENERATED by 02_dv/sim/gen_crc16_beat.py: edit the generator, not this file (`make crc16` checks that the two agree and
//   proves the module equal to the serial chain it replaced: tb_crc16_beat.sv under Verilator and Icarus; run_nic.py I13
//   under neosim). crc_out is the state after the first n words (n = 0 returns crc_in, so a beat's unused word positions are
//   never accumulated), bit-identical to the chain of BW crc16_word stages of drops 0.29-0.40 (crc16_beat_serial in
//   02_dv/tb/): 512 serial XOR steps at BW = 16, the NIC's long path in the synthesis of drop 0.40 (the unit 4,723 ps on
//   ASAP7 SS; tile_nic's minimum period 7,662 ps behind its transmit beat). The serial step is linear over GF(2) with no
//   constant term (the 0xFFFF initial value is the caller's crc_in), so with A the state transform of one all-zero word and
//   B the transform of a word into a zero state:
//     T     = the CRC over the beat padded to MAXBW words, the words at positions >= n forced to zero
//           = A^MAXBW crc_in + sum_{k<n} A^(MAXBW-1-k) B w_k = A^(MAXBW-n) CRC_n  (the zero tail multiplies the state by A)
//     CRC_n = (A^(MAXBW-n))^-1 T                                          (A is invertible: the polynomial has a constant term)
//   Each bit of T is one reduction XOR over a fixed subset of the 16 + 32*BW input bits (ROWi; a balanced tree in synthesis,
//   depth ~log2(528) at BW = 16); the words beyond n are masked on their partial parities pi[k] (word k under row i's 32
//   constants) -- the same function as zeroing the words, 16 AND gates per row instead of 512 on the inputs. The inverse is
//   a 16 x 16 matrix indexed by n itself (NROWi; no arithmetic on n in front of it). Padding to MAXBW words makes the index
//   direct and one set of constants serve every BW <= MAXBW:
//     ROWi[32*k +: 32]     = row i of A^(MAXBW-1-k) B     (word k of the beat; a beat of BW words reads positions 0..BW-1)
//     ROWi[32*MAXBW +: 16] = row i of A^MAXBW              (the crc_in part)
//     NROWi[16*n +: 16]    = row i of (A^(MAXBW-n))^-1     (n = 0..MAXBW; the identity above, for every n a CW-bit port holds)
//   An n > BW, which no caller produces, counts the missing words as zeros (up to MAXBW words). Plain constructs only
//   (continuous assignments, part-selects of localparams), which every simulator of the flow takes.
"""


def module_text(row, nrow):
    L = [HEADER.rstrip("\n")]
    L.append("module crc16_beat #(")
    L.append("  parameter int BW = 16,")
    L.append("  parameter int CW = $clog2(BW + 1)            // word count 0..BW")
    L.append(")(")
    L.append("  input  logic [15:0]      crc_in,")
    L.append("  input  logic [32*BW-1:0] words,              // word i at [32*i +: 32]")
    L.append("  input  logic [CW-1:0]    n,                  // words to accumulate, 0..BW")
    L.append("  output logic [15:0]      crc_out")
    L.append(");")
    L.append(f"  localparam int MAXBW = {MAXBW};                         // the longest beat the tables cover (a bank row); BW <= MAXBW")
    L.append(f"  localparam int MW    = {MW};                          // $clog2(MAXBW + 1): NROWi has an entry for every n of up to MW bits")
    for i in range(16):
        L.append(f"  localparam logic [16+32*MAXBW-1:0] ROW{i:<2d} = {hexlit(16 + 32 * MAXBW, row[i])};")
    for i in range(16):
        L.append(f"  localparam logic [16*(1<<MW)-1:0]  NROW{i:<2d} = {hexlit(16 * (1 << MW), nrow[i])};")
    L.append("  logic [BW-1:0] th;                            // th[k]: word k is accumulated (k < n)")
    L.append("  logic [BW-1:0] " + ", ".join(f"p{i}" for i in range(16)) + ";   // pi[k]: word k's part of T bit i")
    L.append("  logic [15:0]   t;                             // A^(MAXBW-n) CRC_n")
    L.append("  generate")
    L.append("    for (genvar k = 0; k < BW; k++) begin : g_word")
    L.append("      assign th[k] = (n > CW'(k));")
    for i in range(16):
        L.append(f"      assign p{i}[k]{' ' * (i < 10)} = ^(ROW{i}[32*k +: 32] & words[32*k +: 32]);")
    L.append("    end")
    L.append("  endgenerate")
    for i in range(16):
        L.append(f"  assign t[{i:2d}] = ^({{ROW{i}[32*MAXBW +: 16] & crc_in, p{i} & th}});")
    for i in range(16):
        L.append(f"  assign crc_out[{i:2d}] = ^(NROW{i}[16*n +: 16] & t);")
    L.append("endmodule")
    return "\n".join(L) + "\n"


def default_out():
    here = os.path.dirname(os.path.realpath(__file__))
    flat = os.path.join(here, "..", "rtl")                # the flat layout: sim/ beside rtl/
    repo = os.path.join(here, "..", "..", "01_rtl")       # the repository: 02_dv/sim/ and 01_rtl/
    return os.path.normpath(os.path.join(flat if os.path.isdir(flat) else repo, "crc16_beat.sv"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=None, help="the module file (default: rtl/crc16_beat.sv beside sim/, else 01_rtl/)")
    ap.add_argument("--check", action="store_true", help="compare with the file instead of writing it")
    args = ap.parse_args()
    out = args.out or default_out()
    row, nrow = tables()
    bad = self_check(row, nrow)
    ones = [bin(row[i]).count("1") for i in range(16)]
    print(f"gen_crc16_beat: MAXBW = {MAXBW}; the T rows use {min(ones)}..{max(ones)} of the {16 + 32 * MAXBW} input bits; the tables "
          f"through the RTL's expressions vs the serial algorithm at BW 1, 2, 3, 4, 5, 8, 15, 16, every n: {bad} mismatches")
    if bad:
        sys.exit("gen_crc16_beat: the tables do not reproduce the serial CRC -- nothing written")
    text = module_text(row, nrow)
    if args.check:
        try:
            cur = open(out).read()
        except FileNotFoundError:
            cur = None
        if cur != text:
            sys.exit(f"gen_crc16_beat: {out} is not the generator's output -- run python3 sim/gen_crc16_beat.py")
        print(f"gen_crc16_beat: {out} is the generator's output")
        return 0
    with open(out, "w") as f:
        f.write(text)
    print(f"gen_crc16_beat: wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
