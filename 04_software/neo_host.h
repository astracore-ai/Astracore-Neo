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
#define NEO_REG_ERR_MASK 0x05
#define NEO_REG_ERR_CAUSE 0x06
#define NEO_REG_PARTITION 0x0B

/* The chip top (drop 0.42; 08_ip_product/neo_top_integration.md, 01_rtl/top_ctrl.sv): the AXI4-Lite control plane's byte
 * addresses. A tile's host_if registers sit in a 1 KB window per tile, n = y * NX + x, register r at byte 4 * r, so a
 * platform's neo_wr_fn / neo_rd_fn for the chip top writes NEO_TOP_TILE_REG(tile.y * nx + tile.x, reg). A disabled tile's
 * window answers SLVERR. */
#define NEO_TOP_TILE_WIN(n)          (0x00000u + 0x400u * (uint32_t)(n))
#define NEO_TOP_TILE_REG(n, r)       (NEO_TOP_TILE_WIN(n) + 4u * (uint32_t)(r))
#define NEO_TOP_NODE_WIN(b)          (0x10000u + 0x400u * (uint32_t)(b))   /* bridge node b: 0x00 CTRL (bit 1: clear parity), 0x04 STATUS (bit 8) */
#define NEO_TOP_GLOBAL               0x20000u
#define NEO_TOP_ID                   (NEO_TOP_GLOBAL + 0x000u)   /* r: 0x4E454F54 'NEOT' */
#define NEO_TOP_VERSION              (NEO_TOP_GLOBAL + 0x004u)   /* r: 0x00000044 */
#define NEO_TOP_CONFIG               (NEO_TOP_GLOBAL + 0x008u)   /* r: {COLS, ROWS, N_HOST, N_MEM, NY, NX} */
#define NEO_TOP_CONFIG2              (NEO_TOP_GLOBAL + 0x00Cu)   /* r: {log2 bank words, WPF, AXI_DW} */
#define NEO_TOP_SCRATCH              (NEO_TOP_GLOBAL + 0x010u)   /* r/w */
#define NEO_TOP_TIMER_LO             (NEO_TOP_GLOBAL + 0x018u)   /* r: aclk cycles; the read latches TIMER_HI */
#define NEO_TOP_TIMER_HI             (NEO_TOP_GLOBAL + 0x01Cu)
#define NEO_TOP_TILE_EN_LO           (NEO_TOP_GLOBAL + 0x020u)   /* r/w: the tile-enable request, bit n = tile n (reset: all) */
#define NEO_TOP_TILE_EN_HI           (NEO_TOP_GLOBAL + 0x024u)
#define NEO_TOP_TILE_EN_EFF_LO       (NEO_TOP_GLOBAL + 0x028u)   /* r: the map in effect (applied while the tile is idle or the mesh in reset) */
#define NEO_TOP_TILE_EN_EFF_HI       (NEO_TOP_GLOBAL + 0x02Cu)
#define NEO_TOP_DONE_LO              (NEO_TOP_GLOBAL + 0x030u)   /* r: prog_done && !drain_busy, enabled tiles */
#define NEO_TOP_DONE_HI              (NEO_TOP_GLOBAL + 0x034u)
#define NEO_TOP_ERR_LO               (NEO_TOP_GLOBAL + 0x038u)   /* r: err_pin, enabled tiles */
#define NEO_TOP_ERR_HI               (NEO_TOP_GLOBAL + 0x03Cu)
#define NEO_TOP_IRQ_STATUS           (NEO_TOP_GLOBAL + 0x040u)   /* r, write 1 clears: bit 0 done, 1 err, 2 bridge */
#define NEO_TOP_IRQ_MASK             (NEO_TOP_GLOBAL + 0x044u)   /* r/w: 1 masks (reset 0x7) */
#define NEO_TOP_STATUS               (NEO_TOP_GLOBAL + 0x048u)   /* r: bit 0 irq, bit 1 err */
#define NEO_TOP_BR(b, off)           (NEO_TOP_GLOBAL + 0x100u + 0x20u * (uint32_t)(b) + (uint32_t)(off))
#define NEO_TOP_BR_WIN_LO            0x0u                        /* window base bits 31:6 */
#define NEO_TOP_BR_WIN_HI            0x4u                        /* window base bits 39:32 */
#define NEO_TOP_BR_CTRL              0x8u                        /* bits 3:0: the host role's partition */
#define NEO_TOP_BR_ERR               0xCu                        /* r, write 1 clears: 0 crc, 1 lost, 2 axi, 3 timeout, 4 host; 5 parity (level) */
#define NEO_TOP_IRQ_DONE             0x1u
#define NEO_TOP_IRQ_ERR              0x2u
#define NEO_TOP_IRQ_BRIDGE           0x4u
/* A host bridge's AXI4 slave window: the tile index above the bank's byte offset (baw = log2 of the bank's words). A DMA
 * instruction addresses a bridge at x = NX (the bridge column), y = its row, a 20-bit word address in its 4 MB window at
 * the bridge's window base: byte = win_base + 4 * addr. */
#define NEO_TOP_HOST_ADDR(n, word, baw) (((uint64_t)(n) << ((baw) + 2)) | ((uint64_t)(word) << 2))

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
    int nchk;                            /* drop 0.42: the core's check columns, 1 (0 reads as 1) or 4 (the sparse-capable core) */
    int sparse;                          /* drop 0.42: a 2:4 sparse layer (nchk 4): 2*rows-channel tiles, two entries per pixel */
} neo_group_t;

typedef struct {
    neo_tile_t tile;
    int role;                            /* 0 solo, 1 owner, 2 contributor */
    uint16_t cfg[NEO_NDESC];             /* h,w,ho,wo,oy0,oy_n,iy0,s,p,k,ct_n,ct0,ky0,kx0,rn,contrib_n,tile_pixels,regions_m1 ... */
    uint32_t cfg_m;
    uint64_t prog[NEO_MAX_PROG];
    int nprog;
} neo_tile_prog_t;

/* descriptor register order (matches neo_tile's cfg_* ports and host_if 0x10..0x23); D_FLAGS (0x22, reserved before
 * drop 0.42): bit 0 the layer is 2:4 sparse */
enum { D_H, D_W, D_HO, D_WO, D_OY0, D_OY_N, D_IY0, D_S, D_P, D_K, D_CT_N, D_CT0, D_KY0, D_KX0, D_RN,
       D_CONTRIB_N, D_TILE_PIXELS, D_REGIONS_M1, D_FLAGS, D_RSV2 };
#define NEO_FLAG_SPARSE 1u

uint64_t neo_ins(int op, int x, int y, uint32_t addr, uint32_t len, uint32_t base, uint32_t arg);

/* Compile one group over `shares` tiles: owner first, contributors on the nearest free tiles.
 * free[] lists candidate tiles (excluding the owner); nfree their count. Returns the number of tile
 * programs written to out[] (<= shares), -1 on error (no free tile, program memory full), or -2 if an emitted
 * instruction is invalid (a zero-length fetch, drop 0.29). */
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
