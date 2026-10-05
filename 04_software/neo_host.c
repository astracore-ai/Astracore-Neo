/* neo_host.c -- see neo_host.h. Mirrors compiler/neo_backend.py compile_group() */
#include "neo_host.h"

uint64_t neo_ins(int op, int x, int y, uint32_t addr, uint32_t len, uint32_t base, uint32_t arg) {
    return ((uint64_t)op << 60) | ((uint64_t)x << 56) | ((uint64_t)y << 52) | ((uint64_t)(addr & 0xFFFFF) << 32) |
           ((uint64_t)(len & 0xFFF) << 20) | ((uint64_t)(base & 0xFFFF) << 4) | (arg & 0xF);
}

static int ceil_div(int a, int b) { return (a + b - 1) / b; }
static int ilog2_floor(int v) { int r = 0; while (v > 1) { v >>= 1; r++; } return r; }

static int nearest_free(neo_tile_t owner, const neo_tile_t *free, const int *used, int nfree, int nx) {
    int best = -1, best_d = 1 << 30, best_i = 1 << 30;
    for (int i = 0; i < nfree; i++) {
        if (used[i]) continue;
        int d = (free[i].x > owner.x ? free[i].x - owner.x : owner.x - free[i].x) +
                (free[i].y > owner.y ? free[i].y - owner.y : owner.y - free[i].y);
        int idx = free[i].y * nx + free[i].x;
        if (d < best_d || (d == best_d && idx < best_i)) { best = i; best_d = d; best_i = idx; }
    }
    return best;
}

int neo_compile_group(const neo_group_t *g, neo_tile_t owner, const neo_tile_t *free, int nfree, int shares,
                      neo_tile_prog_t *out) {
    int ho = (g->h + 2 * g->p - g->k) / g->s + 1;
    int wo = (g->w + 2 * g->p - g->k) / g->s + 1;
    int cin_tiles = ceil_div(g->cin, g->rows);
    int wpa = g->rows * 8 / 32;
    int wcw = 8 + ilog2_floor(g->rows - 1) + 1 + (((g->rows - 1) & (g->rows - 2)) ? 1 : 0); /* bit_length(rows-1)+9 */
    /* bit_length(rows-1): ilog2_floor(rows-1)+1 for rows-1 >= 1 */
    wcw = 8 + (g->rows <= 1 ? 0 : ilog2_floor(g->rows - 1) + 1) + 1;
    int wpw = (g->cols * 8 + wcw + 31) / 32;
    int apt = g->h * g->w * wpa, wpt_run = g->rows * wpw;
    int iy_lo = g->oy0 * g->s - g->p; if (iy_lo < 0) iy_lo = 0;
    int iy_hi = (g->oy0 + g->oy_n - 1) * g->s + g->k - 1 - g->p; if (iy_hi > g->h - 1) iy_hi = g->h - 1;
    int nrows = iy_hi - iy_lo + 1, region_pixels = nrows * g->w;
    int runs = cin_tiles * g->k * g->k;
    if (shares > runs) shares = runs;
    if (shares > 1 + nfree) shares = 1 + nfree;
    if (shares < 1) shares = 1;
    if (shares > NEO_MAX_SHARES) return -1;
    int M = g->oy_n * wo;
    int used[NEO_MAX_SHARES * 4] = {0};
    int n = 0;
    for (int i = 0; i < shares; i++) {
        int r0 = (i * runs) / shares, r1 = ((i + 1) * runs) / shares;
        if (r1 <= r0) continue;
        neo_tile_prog_t *tp = &out[n];
        int ct0 = r0 / (g->k * g->k), ky0 = (r0 % (g->k * g->k)) / g->k, kx0 = r0 % g->k;
        int ct_lo = r0 / (g->k * g->k), ct_hi = (r1 - 1) / (g->k * g->k);
        if (i == 0) tp->tile = owner;
        else { int f = nearest_free(owner, free, used, nfree, g->nx); if (f < 0) return -1; used[f] = 1; tp->tile = free[f]; }
        tp->role = (i == 0) ? (shares > 1 ? 1 : 0) : 2;
        uint16_t *c = tp->cfg;
        c[D_H] = g->h; c[D_W] = g->w; c[D_HO] = ho; c[D_WO] = wo; c[D_OY0] = g->oy0; c[D_OY_N] = g->oy_n; c[D_IY0] = iy_lo;
        c[D_S] = g->s; c[D_P] = g->p; c[D_K] = g->k; c[D_CT_N] = cin_tiles; c[D_CT0] = ct0; c[D_KY0] = ky0; c[D_KX0] = kx0;
        c[D_RN] = r1 - r0; c[D_CONTRIB_N] = (i == 0) ? shares - 1 : 0; c[D_TILE_PIXELS] = region_pixels; c[D_REGIONS_M1] = 0xFFFF;
        c[D_RSV1] = 0; c[D_RSV2] = 0;
        tp->cfg_m = M;
        tp->nprog = 0;
        for (int ct = ct_lo; ct <= ct_hi; ct++)
            tp->prog[tp->nprog++] = neo_ins(NEO_OP_FETCH_A, g->act_bank.x, g->act_bank.y,
                                            g->act_base + ct * apt + iy_lo * g->w * wpa, region_pixels * wpa, ct * region_pixels, 0);
        tp->prog[tp->nprog++] = neo_ins(NEO_OP_FETCH_W, g->w_bank.x, g->w_bank.y,
                                        g->w_base + (g->nt * runs + r0) * wpt_run, (r1 - r0) * wpt_run, 0, 0);
        if (i != 0) {
            tp->prog[tp->nprog++] = neo_ins(NEO_OP_GO, 0, 0, 0, 0, 0, 0);
            tp->prog[tp->nprog++] = neo_ins(NEO_OP_WAIT_RDY, 0, 0, 0, 0, 0, 0);
            tp->prog[tp->nprog++] = neo_ins(NEO_OP_DRAIN_PSUM, owner.x, owner.y, 0, M, 0, 0);
            tp->prog[tp->nprog++] = neo_ins(NEO_OP_WAIT_DONE, 0, 0, 0, 0, 0, 0);
            tp->prog[tp->nprog++] = neo_ins(NEO_OP_END, 0, 0, 0, 0, 0, 0);
        }
        n++;
    }
    /* finish the owner's program now that the contributors are placed */
    neo_tile_prog_t *ow = &out[0];
    ow->prog[ow->nprog++] = neo_ins(NEO_OP_DRAIN_WR, owner.x, owner.y, g->out_base, M, 0, g->int8_out ? 1 : 0);
    ow->prog[ow->nprog++] = neo_ins(NEO_OP_GO, 0, 0, 0, 0, 0, 0);
    if (n > 1) {
        ow->prog[ow->nprog++] = neo_ins(NEO_OP_WAIT_REDUCE, 0, 0, 0, 0, 0, 0);
        for (int i = 1; i < n; i++) ow->prog[ow->nprog++] = neo_ins(NEO_OP_NOTIFY, out[i].tile.x, out[i].tile.y, 0, 0, 0, 0);
    }
    ow->prog[ow->nprog++] = neo_ins(NEO_OP_WAIT_DONE, 0, 0, 0, 0, 0, 0);
    ow->prog[ow->nprog++] = neo_ins(NEO_OP_END, 0, 0, 0, 0, 0, 0);
    if (ow->nprog > NEO_MAX_PROG) return -1;
    return n;
}

void neo_load_and_start(const neo_tile_prog_t *tp, neo_wr_fn wr, void *ctx) {
    for (int i = 0; i < NEO_NDESC; i++) wr(ctx, tp->tile, NEO_REG_CFG_BASE + i, tp->cfg[i]);
    wr(ctx, tp->tile, NEO_REG_CFG_M, tp->cfg_m);
    for (int i = 0; i < tp->nprog; i++) {
        wr(ctx, tp->tile, NEO_REG_PROG_ADDR, (uint32_t)i);
        wr(ctx, tp->tile, NEO_REG_PROG_LO, (uint32_t)(tp->prog[i] & 0xFFFFFFFFu));
        wr(ctx, tp->tile, NEO_REG_PROG_HI, (uint32_t)(tp->prog[i] >> 32));
    }
    wr(ctx, tp->tile, NEO_REG_CTRL, 1u);
}

uint32_t neo_wait_done(neo_tile_t tile, neo_rd_fn rd, void *ctx, int max_polls) {
    uint32_t st = 0;
    for (int i = 0; i < max_polls; i++) {
        st = rd(ctx, tile, NEO_REG_STATUS);
        if ((st & 1u) && !(st & 2u)) return st;
    }
    return st | 0x80000000u;   /* timeout */
}
