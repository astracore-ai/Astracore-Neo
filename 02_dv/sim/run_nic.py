#!/usr/bin/env python3
"""run_nic.py -- the tile interface's bank-side paths at 16 words per cycle under neosim (drop 0.29, stage 2 of the 512-bit
bank port). tile_nic alone, its link side driven with beats and its bank side modelled as a word array:
  I1 writeback: WRHDR to an unaligned address, WRDATA beats of 1..16 words that start inside a row and cross row boundaries,
     then the CRC flit: every word lands at its address, the masked lanes only, no crc_err / lost_err; a wrong count flags lost_err
  I2 serve: RDREQ for len words from an unaligned address: the beats carry the words in order (partial first beat, full rows,
     partial last), hdr[13:8] = words - 1, and the CRC flit's count and CRC-16 match a software model
  I3 serve under back-pressure: the link accepts every third beat only; the data and the CRC are unchanged
  I6 a zero-length request is answered by its CRC flit alone (count 0, the initial CRC)
  I7 the dead-server fault of M9b: tx_state held in X_SERVE_RD takes the request and never answers it; released, it serves
  I8/I9 fetch receive at entry rate (drop 0.30), two entries per cycle (drop 0.32) at 16x8 and 32x32: activation and weight entries
     assembled from beats of random sizes land at base + index with the right bytes and check values, pairs at even addresses,
     a single entry at an odd base, a beat held at most ceil(words / WPE) cycles, flags clean
  I10 a response with no fetch outstanding is consumed in one cycle per beat and stored nowhere
  I11 PSUM rows into the reduce port at beat rate (drop 0.31): beats of random column counts, the FIFO-full stall on the check word
  I12 the drain in 16-word beats (drop 0.31): INT32 rows as 16 + 16 + 1, INT8 rows, PSUM rows with tags and column headers
  I4/I5 link_packer -> link_unpacker back to back at 32 and 1 words per flit: random beats, PSUM rows and single-word
     types come out as the words, columns and types that went in, no flit wider than WPF"""
import os, random, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from neosim import Design, load
rtl = os.path.join(HERE, "..", "rtl")
BW, LANES = 16, 16
XW = YW = 2; NX = NY = 2; BAW = 12; BRAW = BAW - 4
DW = 32 + 32 * BW; VW = 32 * BW; HW = 2 * (XW + YW) + 8; FW = 1 + HW + DW
T_RDREQ, T_RDRSP, T_WRHDR, T_WRDATA, T_PSUM, T_CRC, T_RDY = 1, 2, 3, 4, 5, 6, 7


def crc16(words, crc=0xFFFF):
    for w in words:
        for b in range(31, -1, -1):
            fb = ((crc >> 15) & 1) ^ ((w >> b) & 1)
            crc = ((crc << 1) & 0xFFFF) ^ (0x1021 if fb else 0)
    return crc


def flit(typ, words, hdr=0, tag=0, src=(1, 1), dst=(0, 0), partition=0):
    """A beat flit as link_unpacker presents it: {parity, dst_y, dst_x, src_y, src_x, seq[7:0], type, tag, hdr, words}."""
    vec = 0
    for i, w in enumerate(words):
        vec |= (w & 0xFFFFFFFF) << (32 * i)
    if typ in (T_RDRSP, T_WRDATA, T_PSUM):
        hdr = (hdr & ~0x3F00) | ((len(words) - 1) << 8)
    body = vec | (hdr << VW) | (tag << (VW + 20)) | (typ << (VW + 29))
    body |= partition << DW
    body |= src[0] << (DW + 8) | src[1] << (DW + 8 + XW) | dst[0] << (DW + 8 + XW + YW) | dst[1] << (DW + 8 + 2 * XW + YW)
    par = bin(body).count("1") & 1
    return body | (par << (FW - 1))


class Nic:
    def __init__(self, rows=16, cols=8):
        self.d = Design(load([os.path.join(rtl, f) for f in ("crc16_word.sv", "crc16_beat.sv", "tile_nic.sv")]))
        self.rows, self.cols = rows, cols
        self.wcw = 8 + (rows - 1).bit_length() + 1
        self.wpa, self.wpw = rows * 8 // 32, (cols * 8 + self.wcw + 31) // 32
        self.d.elaborate("tile_nic", {"XW": XW, "YW": YW, "NX": NX, "NSRC": NX * NY, "BW": BW,
                                       "ROWS": rows, "COLS": cols, "IDXW": 6, "AW": 8, "WAW": 9, "BAW": BAW, "DFD": 64, "FETCH_TIMEOUT": 8192})
        d = self.d
        self.p = {n: d.cell_of(n) for n in ("rst_n", "my_x", "my_y", "rx_valid", "rx_flit", "rx_ready", "tx_valid", "tx_flit", "tx_ready", "b_we", "b_wrow",
                                           "b_wmask", "b_wdata", "b_re", "b_rrow", "b_rdata", "b_rvalid", "ext_ready", "rd_valid", "rq_valid",
                                           "cmd_valid", "cmd_op", "err_clear", "rdy_clear", "partition", "crc_err_sticky", "lost_err_sticky",
                                           "iso_err_sticky")}
        for n in ("abuf_we", "abuf_waddr", "wbuf_we", "wbuf_waddr", "wcbuf_wdata", "cmd_op", "cmd_x", "cmd_y", "cmd_addr", "cmd_len", "cmd_base",
                  "cmd_int8", "cmd_busy", "fetch_timeout_sticky", "abuf_we2", "wbuf_we2", "wcbuf_wdata2"):
            self.p[n] = d.cell_of(n)
        self.abuf_wdata, self.wbuf_wdata = d.array_of("abuf_wdata"), d.array_of("wbuf_wdata")
        self.abuf_wdata2, self.wbuf_wdata2 = d.array_of("abuf_wdata2"), d.array_of("wbuf_wdata2")
        self.pairs = 0                                   # cycles that wrote two entries (drop 0.32)
        for n in ("ext_valid", "ext_idx", "ext_chk", "rd_chk", "drain_busy"):
            self.p[n] = d.cell_of(n)
        self.ext_y, self.rd_data, self.rq_q = d.array_of("ext_y"), d.array_of("rd_data"), d.array_of("rq_q")
        self.ext_rows = []                               # rows popped from the reduce port: (idx, [cols], chk)
        for n in ("rx_valid", "tx_ready", "b_rvalid", "ext_ready", "rd_valid", "rq_valid", "cmd_valid", "err_clear", "rdy_clear", "partition",
                  "cmd_op", "cmd_x", "cmd_y", "cmd_addr", "cmd_len", "cmd_base", "cmd_int8"):
            self.p[n].v = 0
        self.p["my_x"].v = 0; self.p["my_y"].v = 0       # the tile under test sits at (0,0): coordinates are ports since drop 0.35
        self.mem = {}                                   # word address -> word (the bank, as the row port sees it)
        self.abuf, self.wbuf = [], []                   # buffer writes seen (address, bytes[, check])
        self.p["rst_n"].v = 0; d.tick(); d.tick(); self.p["rst_n"].v = 1; d.tick()
        self.pending_read = None

    def tick(self):
        """One clock with the bank model: writes land at the edge, a read returns its row the next cycle. Buffer writes
        (abuf_we / wbuf_we) are recorded as (address, bytes[, check]) in self.abuf / self.wbuf."""
        p = self.p
        self.d.settle()
        if p["ext_valid"].v and p["ext_ready"].v:
            self.ext_rows.append((p["ext_idx"].v, [c.v & 0xFFFFFFFF for c in self.ext_y], p["ext_chk"].v & 0xFFFFFFFF))
        if p["abuf_we"].v:
            self.abuf.append((p["abuf_waddr"].v, [c.v & 0xFF for c in self.abuf_wdata]))
        if p["abuf_we2"].v:                              # the pair's upper entry (drop 0.32): only at an even address
            assert p["abuf_we"].v and p["abuf_waddr"].v % 2 == 0, "abuf_we2 without an even-address first entry"
            self.abuf.append((p["abuf_waddr"].v + 1, [c.v & 0xFF for c in self.abuf_wdata2])); self.pairs += 1
        if p["wbuf_we"].v:
            self.wbuf.append((p["wbuf_waddr"].v, [c.v & 0xFF for c in self.wbuf_wdata], p["wcbuf_wdata"].v & ((1 << self.wcw) - 1)))
        if p["wbuf_we2"].v:
            assert p["wbuf_we"].v and p["wbuf_waddr"].v % 2 == 0, "wbuf_we2 without an even-address first entry"
            self.wbuf.append((p["wbuf_waddr"].v + 1, [c.v & 0xFF for c in self.wbuf_wdata2], p["wcbuf_wdata2"].v & ((1 << self.wcw) - 1))); self.pairs += 1
        wr = None
        if p["b_we"].v:
            wr = (p["b_wrow"].v, p["b_wmask"].v, p["b_wdata"].v)
        rd = p["b_rrow"].v if p["b_re"].v else None
        self.d.tick()
        if wr is not None:
            row, mask, data = wr
            for l in range(LANES):
                if (mask >> l) & 1:
                    self.mem[row * LANES + l] = (data >> (32 * l)) & 0xFFFFFFFF
        if rd is not None:
            vec = 0
            for l in range(LANES):
                vec |= self.mem.get(rd * LANES + l, 0) << (32 * l)
            p["b_rdata"].v = vec; p["b_rvalid"].v = 1
        else:
            p["b_rvalid"].v = 0
        self.d.settle()

    def fetch(self, op, x, y, addr, length, base):
        """One FETCH_A (1) / FETCH_W (2) command on the command port (the DMA engine's cycle)."""
        p = self.p
        p["cmd_valid"].v = 1; p["cmd_op"].v = op; p["cmd_x"].v = x; p["cmd_y"].v = y; p["cmd_addr"].v = addr; p["cmd_len"].v = length; p["cmd_base"].v = base
        self.tick()
        p["cmd_valid"].v = 0
        # the RDREQ goes out on the link
        p["tx_ready"].v = 1
        for _ in range(4):
            self.tick()
        p["tx_ready"].v = 0

    def send(self, f):
        """Present one beat flit until the interface takes it; returns the cycles it was held."""
        p = self.p
        p["rx_valid"].v = 1; p["rx_flit"].v = f
        n = 0
        while True:
            self.d.settle()
            took = p["rx_ready"].v
            self.tick(); n += 1
            if took:
                break
            if n > 100:
                raise RuntimeError("beat not taken")
        p["rx_valid"].v = 0
        return n

    def collect(self, limit=2000, accept=lambda n: True):
        """Run until the CRC flit is accepted on the link side; returns the beats (type, hdr, tag, words)."""
        p = self.p
        out, n = [], 0
        while True:
            self.d.settle()
            p["tx_ready"].v = 1 if accept(n) else 0
            self.d.settle()
            if p["tx_valid"].v and p["tx_ready"].v:
                f = p["tx_flit"].v
                typ = (f >> (VW + 29)) & 7; tag = (f >> (VW + 20)) & 0x1FF; hdr = (f >> VW) & 0xFFFFF
                cnt = ((hdr >> 8) & 0x3F) + 1 if typ in (T_RDRSP, T_WRDATA, T_PSUM) else 1
                words = [(f >> (32 * i)) & 0xFFFFFFFF for i in range(cnt)]
                out.append((typ, hdr, tag, words))
                self.tick(); n += 1
                if typ == T_CRC:
                    break
            else:
                self.tick(); n += 1
            if n > limit:
                raise RuntimeError("no CRC flit")
        p["tx_ready"].v = 0
        return out, n


fails = 0
rng = random.Random(29)

# ---------------- I1: writeback ----------------
nic = Nic()
addr0 = 0x123                                       # row 18, lane 3
words = [rng.getrandbits(32) for _ in range(200)]
nic.send(flit(T_WRHDR, [addr0]))
pos, beats, held = 0, 0, []
while pos < len(words):
    n = min(rng.choice([1, 3, 7, 13, 16, 16, 16]), len(words) - pos)
    held.append(nic.send(flit(T_WRDATA, words[pos:pos + n])))
    pos += n; beats += 1
nic.send(flit(T_CRC, [(len(words) << 16) | crc16(words)]))
nic.tick()
got = [nic.mem.get(addr0 + i) for i in range(len(words))]
spill = [a for a in nic.mem if a < addr0 or a >= addr0 + len(words)]
ok1 = got == words and not spill and nic.p["crc_err_sticky"].v == 0 and nic.p["lost_err_sticky"].v == 0
# a lost beat: the same stream with one beat dropped, the count at the CRC flit disagrees
nic.send(flit(T_WRHDR, [addr0 + 512]))
nic.send(flit(T_WRDATA, words[:16])); nic.send(flit(T_WRDATA, words[32:48]))
nic.send(flit(T_CRC, [(48 << 16) | crc16(words[:48])]))
nic.tick()
ok1b = nic.p["lost_err_sticky"].v == 1 and nic.p["crc_err_sticky"].v == 1
print(f"  I1 writeback: {len(words)} words in {beats} beats of 1..16 words from word {addr0} (lane {addr0 % 16}): every word at its address, "
      f"no spill outside the range, max {max(held)} cycles per beat (2 = crossing a row), flags clean -> {'PASS' if ok1 else 'FAIL'}; "
      f"a dropped beat -> lost_err {nic.p['lost_err_sticky'].v}, crc_err {nic.p['crc_err_sticky'].v} -> {'PASS' if ok1b else 'FAIL'}")
fails += not (ok1 and ok1b)

# ---------------- I2: serve ----------------
nic = Nic()
base, length = 0x2C5, 77                             # lane 5 of row 44, 77 words: 11 + 16 + 16 + 16 + 16 + 2
data = [rng.getrandbits(32) for _ in range(length)]
for i, w in enumerate(data):
    nic.mem[base + i] = w
nic.send(flit(T_RDREQ, [(length << 20) | base], hdr=(1 << 4) | 1, src=(1, 1)))   # requester (1,1)
beats, cycles = nic.collect()
rsp = [b for b in beats if b[0] == T_RDRSP]
sizes = [len(b[3]) for b in rsp]
stream = [w for b in rsp for w in b[3]]
crcf = beats[-1]
ok2 = (stream == data and sizes == [11, 16, 16, 16, 16, 2] and all(((b[1] >> 8) & 0x3F) == len(b[3]) - 1 for b in rsp)
       and crcf[0] == T_CRC and (crcf[3][0] >> 16) == length and (crcf[3][0] & 0xFFFF) == crc16(data))
print(f"  I2 serve: {length} words from word {base} (lane {base % 16}) in beats {sizes} (expect [11, 16, 16, 16, 16, 2]), words in order, "
      f"CRC flit count {crcf[3][0] >> 16} / crc 0x{crcf[3][0] & 0xFFFF:04x} vs model 0x{crc16(data):04x}, {cycles} cycles -> {'PASS' if ok2 else 'FAIL'}")
fails += not ok2

# ---------------- I3: serve with the link accepting every third cycle ----------------
nic = Nic()
for i, w in enumerate(data):
    nic.mem[base + i] = w
nic.send(flit(T_RDREQ, [(length << 20) | base], hdr=(1 << 4) | 1, src=(1, 1)))
beats, cycles3 = nic.collect(accept=lambda n: n % 3 == 2)
stream3 = [w for b in beats if b[0] == T_RDRSP for w in b[3]]
crcf = beats[-1]
ok3 = stream3 == data and (crcf[3][0] >> 16) == length and (crcf[3][0] & 0xFFFF) == crc16(data)
print(f"  I3 serve with back-pressure (link accepts every third cycle): same words and CRC, {cycles3} cycles -> {'PASS' if ok3 else 'FAIL'}")
fails += not ok3


# ---------------- I6: a zero-length request is answered by its CRC flit alone ----------------
nic = Nic()
nic.send(flit(T_RDREQ, [(0 << 20) | 0x40], hdr=(1 << 4) | 1, src=(1, 1)))
beats, cycles6 = nic.collect()
ok6 = len(beats) == 1 and beats[0][0] == T_CRC and beats[0][3][0] == 0xFFFF and nic.p["tx_valid"].v == 0
print(f"  I6 zero-length request: {len(beats)} flit(s) back, type {beats[0][0]} (6 = CRC) with count {beats[0][3][0] >> 16} and crc "
      f"0x{beats[0][3][0] & 0xFFFF:04x} (the initial 0xFFFF), {cycles6} cycles -> {'PASS' if ok6 else 'FAIL'}")
fails += not ok6

# ---------------- I7: the dead-server fault model (tx_state held in X_SERVE_RD) ----------------
nic = Nic()
for i, w in enumerate(data):
    nic.mem[base + i] = w
ts = nic.d.cells["top.tx_state"]
sb = nic.d.cells["top.serve_busy"]
ts.v = 2
nic.send(flit(T_RDREQ, [(length << 20) | base], hdr=(1 << 4) | 1, src=(1, 1)))
emitted, taken = 0, sb.v
nic.p["tx_ready"].v = 1
for _ in range(200):
    ts.v = 2                                             # the hold, re-applied before every edge as the wrapper's falling-edge fault does
    nic.d.settle()
    emitted += nic.p["tx_valid"].v
    nic.tick()
held_busy = sb.v
nic.p["tx_ready"].v = 0
beats, cycles7 = nic.collect()                            # released: the engine goes on from the read state
stream7 = [w for b in beats if b[0] == T_RDRSP for w in b[3]]
crcf = beats[-1]
ok7 = taken == 1 and emitted == 0 and held_busy == 1 and stream7 == data and (crcf[3][0] >> 16) == length and (crcf[3][0] & 0xFFFF) == crc16(data)
print(f"  I7 dead server (tx_state held in X_SERVE_RD): request taken (serve_busy {taken}), nothing emitted in 200 cycles ({emitted} valid), "
      f"still busy {held_busy}; released -> the {length}-word response and its CRC arrive intact -> {'PASS' if ok7 else 'FAIL'}")
fails += not ok7

# ---------------- I8/I9: fetch receive at entry rate (drop 0.30), 16x8 and 32x32 entries ----------------
def entry_bytes(words, wpe):
    return [(words[k // 4] >> (8 * (k % 4))) & 0xFF for k in range(4 * wpe)]


def fetch_test(rows, cols, is_w, n_entries, seed, src=(1, 1), sizes=(1, 2, 5, 9, 16, 16, 16), base=None):
    """A FETCH command for n_entries entries, the response as RDRSP beats of random sizes from `src`, then the CRC flit:
    every entry lands at base + its index with the right bytes (and check value), one entry per cycle at most, the beat
    held for ceil(words / WPE) cycles, flags clean, the fetch complete at the CRC flit."""
    r = random.Random(seed)
    nic = Nic(rows, cols)
    wpe = nic.wpw if is_w else nic.wpa
    words = [r.getrandbits(32) for _ in range(n_entries * wpe)]
    if base is None:
        base = 37 if is_w else 36                       # an odd base (a single first entry, then pairs) and an even one
    nic.fetch(2 if is_w else 1, src[0], src[1], 0x100, len(words), base)
    pos, held = 0, []
    while pos < len(words):
        n = min(r.choice(list(sizes)), len(words) - pos)
        held.append((n, nic.send(flit(T_RDRSP, words[pos:pos + n], src=src))))
        pos += n
    busy_before = nic.p["cmd_busy"].v
    nic.send(flit(T_CRC, [(len(words) << 16) | crc16(words)], src=src))
    nic.tick()
    got = nic.wbuf if is_w else nic.abuf
    exp = []
    for e in range(n_entries):
        ew = words[e * wpe:(e + 1) * wpe]
        if is_w:
            exp.append((base + e, entry_bytes(ew, wpe)[:cols], (ew[(cols * 8) // 32]) & ((1 << nic.wcw) - 1)))
        else:
            exp.append((base + e, entry_bytes(ew, wpe)[:rows]))
    cycles_ok = all(h <= max(1, -(-n // wpe) + 1) for n, h in held)       # ceil(n / wpe) entry cycles, +1 when a leftover is staged alone
    ok = (got == exp and busy_before == 1 and nic.p["cmd_busy"].v == 0 and nic.p["crc_err_sticky"].v == 0
          and nic.p["lost_err_sticky"].v == 0 and (nic.abuf if is_w else nic.wbuf) == [] and cycles_ok)
    return ok, len(words), len(held), max(h for _, h in held), wpe, nic.pairs


for rows, cols, label in ((16, 8, "I8"), (32, 32, "I9")):
    oka, nw_a, nb_a, mx_a, wpa, pa = fetch_test(rows, cols, False, 12, 100 + rows)
    okw, nw_w, nb_w, mx_w, wpw, pw_ = fetch_test(rows, cols, True, 7, 200 + rows)
    print(f"  {label} fetch receive at {rows}x{cols}: {nw_a} activation words ({wpa} per entry) in {nb_a} beats of 1..16 words -> 12 entries at even base + index, "
          f"{pa} cycles wrote a pair, longest beat held {mx_a} cycles -> {'PASS' if oka else 'FAIL'}; {nw_w} weight words ({wpw} per entry) in {nb_w} beats -> 7 entries "
          f"at odd base + index with check values (a single entry first, then pairs: {pw_}), held at most {mx_w} cycles -> {'PASS' if okw else 'FAIL'}")
    fails += not (oka and okw)
    # the rate (drop 0.32): full 16-word beats at an even base -- activations at 32x32 are two 8-word entries per beat, one
    # cycle per beat; at 16x8 four 4-word entries, two cycles; weights (9 / 3 words) pair up whenever two fit
    okf, nw_f, nb_f, mx_f, wpa_, pf = fetch_test(rows, cols, False, 16, 300 + rows, sizes=(16,), base=40)
    okg, nw_g, nb_g, mx_g, wpw_, pg = fetch_test(rows, cols, True, 16, 400 + rows, sizes=(16,), base=40)
    exp_cyc = -(-16 // (2 * wpa_))
    rate_ok = okf and okg and mx_f == exp_cyc and pf == 8 and pg >= 3     # 9-word weight entries pair up less often: a single entry leaves the next address odd
    print(f"  {label}b full 16-word beats at an even base: {nw_f} activation words -> 16 entries, every beat taken in {mx_f} cycle(s) (expect {exp_cyc}), "
          f"{pf} pair writes (expect 8); {nw_g} weight words -> 16 entries, beats held at most {mx_g} cycles, {pg} pair writes -> {'PASS' if rate_ok else 'FAIL'}")
    fails += not rate_ok

# ---------------- I10: a response with no fetch outstanding is consumed and stored nowhere ----------------
nic = Nic()
words = [rng.getrandbits(32) for _ in range(20)]
held = [nic.send(flit(T_RDRSP, words[:16])), nic.send(flit(T_RDRSP, words[16:]))]
nic.send(flit(T_CRC, [(20 << 16) | crc16(words)]))
nic.tick()
ok10 = nic.abuf == [] and nic.wbuf == [] and held == [1, 1] and nic.p["crc_err_sticky"].v == 0 and nic.p["lost_err_sticky"].v == 0
print(f"  I10 response with no fetch outstanding: 20 words in 2 beats taken in {held} cycle(s), no buffer write, CRC and count still checked clean -> {'PASS' if ok10 else 'FAIL'}")
fails += not ok10

# ---------------- I11: PSUM rows into the reduce port at beat rate (drop 0.31) ----------------
def psum_rx_test(rows, cols, seed):
    """Three partial-sum rows (COLS columns + check) arrive as PSUM beats of random sizes, consecutive columns, one row per
    tag; the reduce port is read with ext_ready held low for a while so the two-entry FIFO fills: every row comes out
    with its columns and check value in order, a beat that carries the check word waits while the FIFO is full, every
    other beat is taken in one cycle, flags clean."""
    r = random.Random(seed)
    nic = Nic(rows, cols)
    psum = [[r.getrandbits(32) for _ in range(cols + 1)] for _ in range(3)]
    held = []
    nic.p["ext_ready"].v = 0                               # the owner's reduce port is closed for the first two rows
    for row, ws in enumerate(psum):
        col = 0
        while col <= cols:
            n = min(r.choice([1, 3, 7, 16, 16]), cols + 1 - col)
            if row == 2 and col + n > cols:
                nic.p["ext_ready"].v = 1                   # open it as the third row's check word arrives
            held.append((row, col, n, nic.send(flit(T_PSUM, ws[col:col + n], hdr=col, tag=5 + row))))
            col += n
    nic.send(flit(T_CRC, [(3 * (cols + 1) << 16) | crc16([w for ws in psum for w in ws])]))
    for _ in range(6):
        nic.tick()
    nic.p["ext_ready"].v = 0
    got = [(idx, ys, chk) for idx, ys, chk in nic.ext_rows]
    exp = [(5 + row, ws[:cols], ws[cols]) for row, ws in enumerate(psum)]
    # the beat with row 2's check word was held while the FIFO was full (two rows in it); every other beat took one cycle
    waits = [(row, h) for row, col, n, h in held if h > 1]
    ok = (got == exp and all(row == 2 for row, _ in waits) and len(waits) <= 1
          and nic.p["crc_err_sticky"].v == 0 and nic.p["lost_err_sticky"].v == 0)
    return ok, len(held), waits


for rows, cols, label in ((16, 8, "I11"), (32, 32, "I11b")):
    ok, nb, waits = psum_rx_test(rows, cols, 300 + rows)
    print(f"  {label} PSUM rows into the reduce port at {rows}x{cols}: 3 rows of {cols}+1 words in {nb} beats of 1..16 columns, FIFO full for the "
          f"first two rows: rows out in order with their check values, {'the check-word beat of row 2 held ' + str(waits[0][1]) + ' cycles' if waits else 'no beat held'}, "
          f"every other beat one cycle -> {'PASS' if ok else 'FAIL'}")
    fails += not ok

# ---------------- I12: the drain in 16-word beats (drop 0.31) ----------------
def drain_test(rows, cols, mode, seed):
    """Rows pushed into the drain FIFO (rd_valid / rq_valid as the core does), a DRAIN_WR (INT32 or INT8) or DRAIN_PSUM
    command: the beats on the link carry the rows' words in order with the right counts (16 + 16 + 1 for a 33-word row),
    WRHDR before a bank drain, PSUM beats with the row index as tag and the column of word 0 in hdr, the CRC flit's count
    and CRC over the model's word stream."""
    r = random.Random(seed)
    nic = Nic(rows, cols)
    p = nic.p
    nrows = 3
    data = [[r.getrandbits(32) for _ in range(cols)] for _ in range(nrows)]
    chks = [r.getrandbits(32) for _ in range(nrows)]
    q = [[r.randrange(-128, 128) for _ in range(cols)] for _ in range(nrows)]
    op = 4 if mode == "psum" else 3
    p["cmd_valid"].v = 1; p["cmd_op"].v = op; p["cmd_x"].v = 1; p["cmd_y"].v = 1; p["cmd_addr"].v = 0x3C0; p["cmd_len"].v = nrows
    p["cmd_int8"].v = 1 if mode == "int8" else 0
    nic.tick(); p["cmd_valid"].v = 0
    for i in range(nrows):
        if mode == "int8":
            for j, c in enumerate(nic.rq_q): c.v = q[i][j] & 0xFF
            p["rq_valid"].v = 1
        else:
            for j, c in enumerate(nic.rd_data): c.v = data[i][j]
            p["rd_chk"].v = chks[i]; p["rd_valid"].v = 1
        nic.tick()
        p["rq_valid"].v = 0; p["rd_valid"].v = 0
    beats, cycles = nic.collect()
    if mode == "int8":
        words = [sum((q[i][4 * k + b] & 0xFF) << (8 * b) for b in range(4)) for i in range(nrows) for k in range(cols // 4)]
        row_len = cols // 4
    else:
        words = [w for i in range(nrows) for w in data[i] + [chks[i]]]
        row_len = cols + 1
    exp_sizes = []
    for i in range(nrows):
        left = row_len
        while left > 0:
            exp_sizes.append(min(16, left)); left -= min(16, left)
    data_beats = [b for b in beats if b[0] in (T_WRDATA, T_PSUM)]
    stream = [w for b in data_beats for w in b[3]]
    sizes = [len(b[3]) for b in data_beats]
    crcf = beats[-1]
    ok = (stream == words and sizes == exp_sizes and crcf[0] == T_CRC and (crcf[3][0] >> 16) == len(words)
          and (crcf[3][0] & 0xFFFF) == crc16(words) and all(((b[1] >> 8) & 0x3F) == len(b[3]) - 1 for b in data_beats)
          and nic.p["drain_busy"].v == 0)
    if mode == "psum":
        cols_ok, k = True, 0
        for i in range(nrows):
            c = 0
            while c < row_len:
                b = data_beats[k]; cols_ok &= (b[2] == i and (b[1] & 0xFF) == c); c += len(b[3]); k += 1
        ok = ok and cols_ok and beats[0][0] == T_PSUM
    else:
        ok = ok and beats[0][0] == T_WRHDR and beats[0][3][0] == 0x3C0
    return ok, sizes, cycles


for rows, cols in ((16, 8), (32, 32)):
    res = {m: drain_test(rows, cols, m, 400 + rows + len(m)) for m in ("int32", "int8", "psum")}
    ok = all(r_[0] for r_ in res.values())
    print(f"  I12 drain in beats at {rows}x{cols}: 3 INT32 rows as beats {res['int32'][1]} ({res['int32'][2]} cycles), 3 INT8 rows as {res['int8'][1]}, "
          f"3 PSUM rows as {res['psum'][1]} with row tags and column headers; words, counts, WRHDR and CRC flits vs the model -> {'PASS' if ok else 'FAIL'}")
    fails += not ok

# ---------------- I4/I5: packer -> unpacker wired back to back, 32 and 1 words per flit ----------------
WRAP = """
module link_pair #(parameter int XW = 2, YW = 2, BW = 16, WPF = 1,
  parameter int DWB = 32 + 32 * BW, DWL = 32 + 32 * WPF, FWB = 1 + 2 * (XW + YW) + 8 + DWB, FWL = 1 + 2 * (XW + YW) + 8 + DWL)(
  input logic clk, input logic rst_n,
  input logic in_valid, input logic [FWB-1:0] in_flit, output logic in_ready,
  output logic out_valid, output logic [FWB-1:0] out_flit, input logic out_ready,
  output logic l_valid, output logic [FWL-1:0] l_flit, output logic l_ready);
  link_packer #(.XW(XW), .YW(YW), .BW(BW), .WPF(WPF)) u_p (.clk(clk), .rst_n(rst_n), .core_valid(in_valid), .core_flit(in_flit),
    .core_ready(in_ready), .link_valid(l_valid), .link_flit(l_flit), .link_ready(l_ready));
  link_unpacker #(.XW(XW), .YW(YW), .BW(BW), .WPF(WPF)) u_u (.clk(clk), .rst_n(rst_n), .link_valid(l_valid), .link_flit(l_flit),
    .link_ready(l_ready), .core_valid(out_valid), .core_flit(out_flit), .core_ready(out_ready));
endmodule
"""


def link_test(wpf, seed):
    """Random beats (data streams of random beat sizes, PSUM rows, single-word types) through link_packer and link_unpacker wired
    back to back; the output side accepts at random. Checks the word stream per type, the PSUM columns, the beat count fields
    (one per 16 words at most), and that no link flit carries more than WPF words."""
    r = random.Random(seed)
    wrap = os.path.join(HERE, "link_pair_tmp.sv")
    open(wrap, "w").write(WRAP)
    try:
        d = Design(load([os.path.join(rtl, "link_pack.sv"), wrap]))
    finally:
        os.remove(wrap)
    d.elaborate("link_pair", {"XW": XW, "YW": YW, "BW": BW, "WPF": wpf})
    p = {n: d.cell_of(n) for n in ("rst_n", "in_valid", "in_flit", "in_ready", "out_valid", "out_flit", "out_ready", "l_valid", "l_flit", "l_ready")}
    p["in_valid"].v = 0; p["out_ready"].v = 0
    p["rst_n"].v = 0; d.tick(); d.tick(); p["rst_n"].v = 1; d.tick()
    # the input: a serve stream, a drain of single-word WRDATA beats, two PSUM rows of 9 columns, single-word flits in between
    seq = []
    serve = [r.getrandbits(32) for _ in range(90)]
    pos = 0
    while pos < len(serve):
        n = min(r.choice([5, 16, 16, 16, 11]), len(serve) - pos)
        seq.append(flit(T_RDRSP, serve[pos:pos + n])); pos += n
    seq.append(flit(T_CRC, [0x12345678]))
    seq.append(flit(T_WRHDR, [0x777]))
    drain = [r.getrandbits(32) for _ in range(40)]
    seq += [flit(T_WRDATA, [w]) for w in drain]
    seq.append(flit(T_CRC, [0xABCD0000 | 40]))
    psum = [[r.getrandbits(32) for _ in range(9)] for _ in range(2)]
    for row, ws in enumerate(psum):
        seq += [flit(T_PSUM, [w], hdr=col, tag=3 + row) for col, w in enumerate(ws)]
    seq.append(flit(T_RDY, [0]))
    seq.append(flit(T_CRC, [0x00120000 | 18]))
    out, flits, i, n = [], [], 0, 0
    while True:                                        # until the third CRC flit has come out
        if i < len(seq):
            p["in_valid"].v = 1; p["in_flit"].v = seq[i]
        else:
            p["in_valid"].v = 0
        p["out_ready"].v = 1 if r.random() < 0.6 else 0
        d.settle()
        if p["l_valid"].v and p["l_ready"].v:
            f = p["l_flit"].v
            vwl = 32 * wpf
            typ = (f >> (vwl + 29)) & 7; hdr = (f >> vwl) & 0xFFFFF
            cnt = ((hdr >> 8) & 0x3F) + 1 if typ in (T_RDRSP, T_WRDATA, T_PSUM) else 1
            flits.append((typ, cnt))
        if p["out_valid"].v and p["out_ready"].v:
            f = p["out_flit"].v
            typ = (f >> (VW + 29)) & 7; tag = (f >> (VW + 20)) & 0x1FF; hdr = (f >> VW) & 0xFFFFF
            cnt = ((hdr >> 8) & 0x3F) + 1 if typ in (T_RDRSP, T_WRDATA, T_PSUM) else 1
            out.append((typ, hdr, tag, [(f >> (32 * k)) & 0xFFFFFFFF for k in range(cnt)]))
        took = p["in_valid"].v and p["in_ready"].v
        d.tick(); n += 1
        if took:
            i += 1
        if len([x for x in out if x[0] == T_CRC]) == 3:
            break
        if n > 5000:
            raise RuntimeError("link test did not drain")
    types = [x[0] for x in out]
    rd = [w for x in out if x[0] == T_RDRSP for w in x[3]]
    wr = [w for x in out if x[0] == T_WRDATA for w in x[3]]
    ps = [(x[2], (x[1] & 0xFF) + k, w) for x in out if x[0] == T_PSUM for k, w in enumerate(x[3])]
    ps_exp = [(3 + row, col, w) for row, ws in enumerate(psum) for col, w in enumerate(ws)]
    singles = [x for x in out if x[0] in (T_CRC, T_WRHDR, T_RDY)]
    ok = (i == len(seq) and rd == serve and wr == drain and ps == ps_exp
          and [x[3][0] for x in singles] == [0x12345678, 0x777, 0xABCD0028, 0, 0x00120012]
          and all(len(x[3]) <= BW for x in out) and all(c <= wpf for _, c in flits)
          and types.index(T_CRC) > max(k for k, t in enumerate(types) if t == T_RDRSP))
    widest = max(c for _, c in flits)
    return ok, len(flits), widest, n

for wpf, label in ((32, "I4"), (1, "I5")):
    ok, nfl, widest, cyc = link_test(wpf, 7 + wpf)
    print(f"  {label} packer -> unpacker at {wpf} words per flit: 90-word serve in mixed beats, 40 drain words, two PSUM rows, single-word "
          f"types in between, output accepted at random: {nfl} link flits (widest {widest}), every word, column and type in order -> {'PASS' if ok else 'FAIL'} ({cyc} cycles)")
    fails += not ok

print(f"RESULT: {'ALL PASS' if fails == 0 else f'{fails} FAILURES'}")
raise SystemExit(1 if fails else 0)
