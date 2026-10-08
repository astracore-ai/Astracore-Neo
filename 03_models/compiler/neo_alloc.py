#!/usr/bin/env python3
"""
neo_alloc.py -- the bank allocator (drop 0.33): where a layer's activations, weights and outputs live in the mesh's banks.

Every bank is BANK_WORDS words (2 MB = 524,288 words in silicon) managed in units of one bank row, ROW = 16 words -- the
unit the 512-bit bank reads and writes, so every region starts on a row and every fetch of whole rows is whole-row
traffic. A tensor (an activation map of `ct` channel tiles, h x w pixels, wpa words per pixel) is placed per channel tile
in bands of consecutive rows, each band a contiguous region in one bank; band boundaries are aligned to the producing
layer's output-row chunks, so every group's output rows fall into one band and one DRAIN_WR, and a consuming group's
input window (iy_lo..iy_hi) is covered by the one to three bands it overlaps, one FETCH_A each. Weights are placed per
output tile as one contiguous block (the group's FETCH_W reads a run range of it) and stay resident for the whole run.
Liveness: a layer's input tensor is released once the layer is compiled; weights never. A tile-exclusion mask removes
tiles from the owner/contributor pool *and* their banks from the allocator (a yielded-out tile has no usable bank).
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

ROW = 16                         # words per bank row = the allocation unit
BANK_WORDS = 524288              # 2 MB per bank (BANK_DEPTH of the silicon configuration)
ADDR_BITS = 20                   # the DMA instruction's bank address field (words)

Bank = Tuple[int, int]


class _FreeList:
    """First-fit free list in rows for one bank."""
    def __init__(self, rows: int):
        self.rows = rows
        self.free: List[Tuple[int, int]] = [(0, rows)]          # (start, length), sorted, disjoint
        self.used = 0
        self.peak = 0

    def largest(self) -> int:
        return max((n for _, n in self.free), default=0)

    def total_free(self) -> int:
        return sum(n for _, n in self.free)

    def alloc(self, n: int) -> Optional[int]:
        for i, (s, m) in enumerate(self.free):
            if m >= n:
                if m == n:
                    del self.free[i]
                else:
                    self.free[i] = (s + n, m - n)
                self.used += n
                self.peak = max(self.peak, self.used)
                return s
        return None

    def release(self, s: int, n: int):
        self.free.append((s, n))
        self.free.sort()
        merged: List[Tuple[int, int]] = []
        for a, b in self.free:
            if merged and merged[-1][0] + merged[-1][1] == a:
                merged[-1] = (merged[-1][0], merged[-1][1] + b)
            else:
                merged.append((a, b))
        self.free = merged
        self.used -= n


@dataclass
class Region:
    """`rows` consecutive pixel rows [y0, y0 + rows) of channel tile `ct`, contiguous at `base` (words) in `bank`."""
    ct: int
    y0: int
    rows: int
    bank: Bank
    base: int                    # word address inside the bank (a multiple of ROW)
    words: int                   # rows * w * wpa, rounded up to whole bank rows


@dataclass
class Tensor:
    ct_tiles: int
    h: int
    w: int
    wpa: int                     # words per pixel (one activation entry)
    regions: Dict[int, List[Region]] = field(default_factory=dict)     # ct -> bands sorted by y0
    name: str = ""

    def segments(self, ct: int, y_lo: int, y_hi: int) -> List[Tuple[Bank, int, int, int]]:
        """The bands covering rows y_lo..y_hi (inclusive) of channel tile ct: (bank, address of row y_a, y_a, y_b)."""
        out = []
        for r in self.regions[ct]:
            a, b = max(y_lo, r.y0), min(y_hi, r.y0 + r.rows - 1)
            if a <= b:
                out.append((r.bank, r.base + (a - r.y0) * self.w * self.wpa, a, b))
        assert out and out[0][2] == y_lo and out[-1][3] == y_hi, f"rows {y_lo}..{y_hi} of ct {ct} not fully placed"
        return out

    def region_of(self, ct: int, y0: int, rows: int) -> Region:
        """The one band holding output rows [y0, y0 + rows) -- bands are aligned to the producer's chunks."""
        for r in self.regions[ct]:
            if r.y0 <= y0 and y0 + rows <= r.y0 + r.rows:
                return r
        raise ValueError(f"rows {y0}..{y0 + rows - 1} of ct {ct} straddle a band boundary")

    @property
    def words(self) -> int:
        return sum(r.words for rs in self.regions.values() for r in rs)

    def banks(self) -> List[Bank]:
        return sorted({r.bank for rs in self.regions.values() for r in rs})


class BankAllocator:
    def __init__(self, nx: int, ny: int, bank_words: int = BANK_WORDS, exclude: Sequence[Bank] = ()):
        self.nx, self.ny = nx, ny
        self.bank_words = bank_words
        self.exclude = set(exclude)
        self.banks: Dict[Bank, _FreeList] = {(x, y): _FreeList(bank_words // ROW)
                                             for y in range(ny) for x in range(nx) if (x, y) not in self.exclude}
        self.resident: List[Tuple[Bank, int, int]] = []          # weights: never released

    # ---- rows and words ----
    @staticmethod
    def rows_for(words: int) -> int:
        return (words + ROW - 1) // ROW

    def alloc(self, words: int, prefer: Optional[Bank] = None) -> Tuple[Bank, int]:
        """A contiguous region of `words` (rounded up to rows): in `prefer` if it has room, else in the bank with the
        largest free run. Raises ValueError when no bank can hold it."""
        n = self.rows_for(words)
        order = []
        if prefer in self.banks:
            order.append(prefer)
        order += sorted((b for b in self.banks if b != prefer), key=lambda b: (-self.banks[b].largest(), b[1] * self.nx + b[0]))
        for b in order:
            s = self.banks[b].alloc(n)
            if s is not None:
                return b, s * ROW
        raise ValueError(f"no bank has {n} free rows ({words} words); largest free run {max(f.largest() for f in self.banks.values())} rows")

    def release(self, bank: Bank, base: int, words: int):
        self.banks[bank].release(base // ROW, self.rows_for(words))

    def release_tensor(self, t: Tensor):
        for rs in t.regions.values():
            for r in rs:
                self.release(r.bank, r.base, r.words)

    # ---- tensors ----
    def place_tensor(self, ct_tiles: int, h: int, w: int, wpa: int, align_rows: int = 1, name: str = "",
                     prefer: Optional[Sequence[Bank]] = None) -> Tensor:
        """Each channel tile in bands of consecutive pixel rows: a band is as many rows as the chosen bank's largest free
        run holds, rounded down to a multiple of `align_rows` (the producer's output-row chunk) so no group's output
        straddles two bands; bands go round the banks with the most room (or the `prefer` list first)."""
        t = Tensor(ct_tiles, h, w, wpa, name=name)
        row_words = w * wpa
        pref = list(prefer or [])
        for ct in range(ct_tiles):
            t.regions[ct] = []
            y = 0
            while y < h:
                left = h - y
                # the bank with the largest free run (preferred banks first while they have room for one aligned band)
                cands = [b for b in pref if b in self.banks] + sorted(self.banks, key=lambda b: (-self.banks[b].largest(), b[1] * self.nx + b[0]))
                placed = False
                for b in cands:
                    cap_rows = (self.banks[b].largest() * ROW) // row_words          # pixel rows that fit the largest run
                    band = min(left, (cap_rows // align_rows) * align_rows) if left > align_rows else min(left, cap_rows)
                    if band <= 0:
                        continue
                    words = band * row_words
                    bank, base = self.alloc(words, prefer=b)
                    t.regions[ct].append(Region(ct, y, band, bank, base, self.rows_for(words) * ROW))
                    y += band
                    placed = True
                    break
                if not placed:
                    raise ValueError(f"{name}: channel tile {ct} rows from {y}: no bank holds even one aligned band of {align_rows} rows "
                                     f"({align_rows * row_words} words)")
        return t

    def place_weights(self, words: int, prefer: Optional[Bank] = None) -> Tuple[Bank, int]:
        bank, base = self.alloc(words, prefer)
        self.resident.append((bank, base, words))
        return bank, base

    # ---- reporting ----
    def occupancy(self) -> Dict[Bank, Tuple[int, int]]:
        """bank -> (rows in use now, peak rows) out of bank_words / ROW."""
        return {b: (f.used, f.peak) for b, f in self.banks.items()}

    def summary(self) -> str:
        occ = self.occupancy()
        rows = self.bank_words // ROW
        peak = max(p for _, p in occ.values())
        now = sum(u for u, _ in occ.values())
        res = sum(w for _, _, w in self.resident)
        return (f"{len(occ)} banks of {rows} rows ({self.bank_words * 4 // 2**20} MB): peak occupancy {peak} rows "
                f"({100.0 * peak / rows:.1f} %) in bank {max(occ, key=lambda b: occ[b][1])}, resident weights {res * 4 / 2**20:.1f} MB, "
                f"in use at the end {now * ROW * 4 / 2**20:.1f} MB" + (f", excluded tiles {sorted(self.exclude)}" if self.exclude else ""))
