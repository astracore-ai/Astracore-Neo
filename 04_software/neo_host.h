/* neo_host.h -- AstraCore Neo host driver (drop 0.11): emits a tile's descriptor and DMA program for
 * one (output tile, output-row range) group, split over `shares` tiles, exactly as the Python backend
 * (compiler/neo_backend.py) does, and loads it through host_if's register map. Pure C99, no malloc;
 * intended for the safety island (Cortex-R52+) and for the PCIe host driver.
 */
#ifndef NEO_HOST_H
#define NEO_HOST_H
#include <stdint.h>

#define NEO_OP_FETCH_A 1
#define NEO_OP_FETCH_W 2
#define NEO_OP_DRAIN_WR 3
#define NEO_OP_DRAIN_PSUM 4
#define NEO_OP_GO 5
#define NEO_OP_WAIT_FREE 6
#define NEO_OP_WAIT_DONE 7
#define NEO_OP_END 8
#define NEO_OP_NOTIFY 9
#define NEO_OP_WAIT_RDY 10
#define NEO_OP_WAIT_REDUCE 11

#define NEO_MAX_PROG 32      /* program memory entries per tile (drop 0.22: 32, was 16) */
#define NEO_MAX_FETCH 4095   /* the DMA instruction's 12-bit length field; longer fetches are split (drop 0.21) */
#define NEO_MAX_SHARES 16
#define NEO_NDESC 20

/* host_if register map (word addresses) */
#define NEO_REG_CTRL 0x00
#define NEO_REG_STATUS 0x01
#define NEO_REG_PROG_ADDR 0x02
#define NEO_REG_PROG_LO 0x03
#define NEO_REG_PROG_HI 0x04
#define NEO_REG_CFG_BASE 0x10
#define NEO_REG_CFG_M 0x24
#define NEO_REG_RQ_TBL 0x30
#define NEO_REG_RQ_ADDR 0x31
#define NEO_REG_RQ_RELU 0x32

typedef struct { int x, y; } neo_tile_t;

typedef struct {
    int h, w, cin, cout, k, s, p;        /* layer */
    int rows, cols;                      /* core geometry */
    int nx, ny;                          /* mesh */
    neo_tile_t act_bank; uint32_t act_base;
    neo_tile_t w_bank;   uint32_t w_base;
    int nt;                              /* output tile */
    int oy0, oy_n;                       /* output-row range */
    int int8_out;                        /* drain requantized INT8 rows (the next layer's input) */
    uint32_t out_base;                   /* word address of the result in the owner's bank */
} neo_group_t;

typedef struct {
    neo_tile_t tile;
    int role;                            /* 0 solo, 1 owner, 2 contributor */
    uint16_t cfg[NEO_NDESC];             /* h,w,ho,wo,oy0,oy_n,iy0,s,p,k,ct_n,ct0,ky0,kx0,rn,contrib_n,tile_pixels,regions_m1 ... */
    uint32_t cfg_m;
    uint64_t prog[NEO_MAX_PROG];
    int nprog;
} neo_tile_prog_t;

/* descriptor register order (matches neo_tile's cfg_* ports and host_if 0x10..0x23) */
enum { D_H, D_W, D_HO, D_WO, D_OY0, D_OY_N, D_IY0, D_S, D_P, D_K, D_CT_N, D_CT0, D_KY0, D_KX0, D_RN,
       D_CONTRIB_N, D_TILE_PIXELS, D_REGIONS_M1, D_RSV1, D_RSV2 };

uint64_t neo_ins(int op, int x, int y, uint32_t addr, uint32_t len, uint32_t base, uint32_t arg);

/* Compile one group over `shares` tiles: owner first, contributors on the nearest free tiles.
 * free[] lists candidate tiles (excluding the owner); nfree their count. Returns the number of tile
 * programs written to out[] (<= shares), or -1 on error. */
int neo_compile_group(const neo_group_t *g, neo_tile_t owner, const neo_tile_t *free, int nfree, int shares,
                      neo_tile_prog_t *out);

/* Register access provided by the platform (memory-mapped or PCIe BAR). */
typedef void (*neo_wr_fn)(void *ctx, neo_tile_t tile, uint32_t reg, uint32_t val);
typedef uint32_t (*neo_rd_fn)(void *ctx, neo_tile_t tile, uint32_t reg);

/* Load a tile program through host_if and start it. */
void neo_load_and_start(const neo_tile_prog_t *tp, neo_wr_fn wr, void *ctx);
/* Poll STATUS until prog_done and !drain_busy; returns the status word. */
uint32_t neo_wait_done(neo_tile_t tile, neo_rd_fn rd, void *ctx, int max_polls);

#endif
