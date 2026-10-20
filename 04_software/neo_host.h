/* neo_host.h -- AstraCore Neo host driver (drop 0.11; drop 0.44: the whole register map and the causes, the runtime's
 * group fields): emits a tile's descriptor and DMA program for
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
#define NEO_REG_PRIO 0x0C          /* drop 0.43: the tile's router priority map, bit p = partition p is the high class */

/* ---- drop 0.44 (stream D3, review finding 6): the whole of host_if.sv's map and the tile's causes, checked against the
 * RTL by 02_dv/sim/check_regmap.py (python-regress): every register of host_if.sv's header is a NEO_REG_* here with the
 * same address, and every bit of neo_tile.sv's tile_flags is a NEO_CAUSE_* with its position (bit 0 the last flag of the
 * concatenation), the watchdog at NFLAGS. Stream S1 adds flags in this block. ---- */
#define NEO_REG_WD_CTRL 0x07       /* w: bit 0 enable, [31:8] the window in cycles */
#define NEO_REG_WD_KICK 0x08       /* w: any write kicks the watchdog */
#define NEO_REG_SELFTEST 0x09      /* w: bit 0 asserts the core's fault-injection hook */
#define NEO_REG_MBIST 0x0A         /* w: any write starts the March C-; r: bit 0 active, 1 done, 2 fail, 3 fail found by ECC, [31:12] address */
#define NEO_REG_VEC_ADDR 0x38      /* w/r: [11:0] the vector unit's table address ([11:8] table: 0 PROG, 1 LUTV, 2 LUTS, 3 SC, 4 BI, 5 K) */
#define NEO_REG_VEC_DATA 0x39      /* w: the entry at VEC_ADDR, then VEC_ADDR + 1 */
#define NEO_REG_CFG_FLAGS (NEO_REG_CFG_BASE + D_FLAGS)   /* 0x22: bit 0 the layer is 2:4 sparse */
#define NEO_STATUS_PROG_DONE 0x1u
#define NEO_STATUS_DRAIN_BUSY 0x2u
#define NEO_STATUS_CORE_DONE 0x4u
/* ERR_CAUSE / ERR_MASK bits (neo_tile.sv's tile_flags, esm NFLAGS = 18; the watchdog above them) */
#define NEO_CAUSE_PARITY_ERR 0
#define NEO_CAUSE_CRC_ERR 1
#define NEO_CAUSE_ARRAY_ABFT 2
#define NEO_CAUSE_ACC_ABFT 3
#define NEO_CAUSE_CTRL_ERR 4
#define NEO_CAUSE_SEQ_ERR 5
#define NEO_CAUSE_RQ_ERR 6
#define NEO_CAUSE_ECC_UE 7
#define NEO_CAUSE_ECC_CE 8
#define NEO_CAUSE_WBUF_CE 9
#define NEO_CAUSE_WBUF_UE 10
#define NEO_CAUSE_RQ_TBL_PERR 11
#define NEO_CAUSE_LOST_ERR 12
#define NEO_CAUSE_FETCH_TIMEOUT 13
#define NEO_CAUSE_BIST_FAIL 14
#define NEO_CAUSE_PROG_CE 15
#define NEO_CAUSE_CTRL_PATH_ERR 16
#define NEO_CAUSE_ISO_ERR 17
#define NEO_CAUSE_NFLAGS 18
#define NEO_CAUSE_WATCHDOG NEO_CAUSE_NFLAGS
#define NEO_CAUSE_MASK_ALL ((1u << (NEO_CAUSE_NFLAGS + 1)) - 1u)
/* the cause names in bit order (neo_host.c), for a driver's decode: neo_cause_name(bit) */
const char *neo_cause_name(int bit);
/* ---- end of the drop 0.44 register block ---- */

/* Tile telemetry (drop 0.43; host_if 0x40 .. 0x4C, 01_rtl/tile_telem.sv): 32-bit saturating counters read as snapshots.
 * TEL_CTRL w: bit 0 snapshot (every counter into its snapshot at one edge), bit 1 clear (the counters restart at 0; both
 * at once close one window and open the next); r: [15:8] the number of counters. TEL(k) r: counter k's snapshot. */
#define NEO_REG_TEL_CTRL 0x40
#define NEO_REG_TEL(k) (0x41 + (k))
enum { NEO_TEL_CYCLES,            /* the window's cycles */
       NEO_TEL_ARRAY_VALID,       /* a row entered the array: MAC utilization = ARRAY_VALID / CYCLES */
       NEO_TEL_FEEDER_STALL,      /* the sequencer in a load / start / run state with no row entering */
       NEO_TEL_FETCH_WAIT,        /* the DMA engine waiting on a FETCH's response */
       NEO_TEL_DRAIN,             /* the NIC sending a drain message */
       NEO_TEL_STALL_N, NEO_TEL_STALL_E, NEO_TEL_STALL_S, NEO_TEL_STALL_W, NEO_TEL_STALL_L,   /* router output valid, not ready */
       NEO_TEL_BANK_RD, NEO_TEL_BANK_WR,                                                      /* the bank's row ports busy */
       NEO_TEL_NCNT };

/* The chip top (drop 0.42; 08_ip_product/neo_top_integration.md, 01_rtl/top_ctrl.sv): the AXI4-Lite control plane's byte
 * addresses. A tile's host_if registers sit in a 1 KB window per tile, n = y * NX + x, register r at byte 4 * r, so a
 * platform's neo_wr_fn / neo_rd_fn for the chip top writes NEO_TOP_TILE_REG(tile.y * nx + tile.x, reg). A disabled tile's
 * window answers SLVERR. */
#define NEO_TOP_TILE_WIN(n)          (0x00000u + 0x400u * (uint32_t)(n))
#define NEO_TOP_TILE_REG(n, r)       (NEO_TOP_TILE_WIN(n) + 4u * (uint32_t)(r))
#define NEO_TOP_NODE_WIN(b)          (0x10000u + 0x400u * (uint32_t)(b))   /* bridge node b: 0x00 CTRL (bit 1: clear parity), 0x04 STATUS (bit 8) */
#define NEO_TOP_GLOBAL               0x20000u
#define NEO_TOP_ID                   (NEO_TOP_GLOBAL + 0x000u)   /* r: 0x4E454F54 'NEOT' */
#define NEO_TOP_VERSION              (NEO_TOP_GLOBAL + 0x004u)   /* r: 0x00000045 (drop 0.43) */
#define NEO_TOP_CONFIG               (NEO_TOP_GLOBAL + 0x008u)   /* r: {COLS, ROWS, N_HOST, N_MEM, NY, NX} */
#define NEO_TOP_CONFIG2              (NEO_TOP_GLOBAL + 0x00Cu)   /* r: {log2 bank words, WPF, AXI_DW} */
#define NEO_TOP_SCRATCH              (NEO_TOP_GLOBAL + 0x010u)   /* r/w */
#define NEO_TOP_TIMER_LO             (NEO_TOP_GLOBAL + 0x018u)   /* r: aclk cycles; the read latches TIMER_HI */
#define NEO_TOP_TIMER_HI             (NEO_TOP_GLOBAL + 0x01Cu)
#define NEO_TOP_TILE_EN_LO           (NEO_TOP_GLOBAL + 0x020u)   /* r/w: the tile-enable (power, drop 0.43) request, bit n = tile n (reset: all) */
#define NEO_TOP_TILE_EN_HI           (NEO_TOP_GLOBAL + 0x024u)
#define NEO_TOP_TILE_EN_EFF_LO       (NEO_TOP_GLOBAL + 0x028u)   /* r: the tiles that are on (applied while the tile is idle or the mesh in reset) */
#define NEO_TOP_TILE_EN_EFF_HI       (NEO_TOP_GLOBAL + 0x02Cu)
#define NEO_TOP_DONE_LO              (NEO_TOP_GLOBAL + 0x030u)   /* r: prog_done && !drain_busy, enabled tiles */
#define NEO_TOP_DONE_HI              (NEO_TOP_GLOBAL + 0x034u)
#define NEO_TOP_ERR_LO               (NEO_TOP_GLOBAL + 0x038u)   /* r: err_pin, enabled tiles */
#define NEO_TOP_ERR_HI               (NEO_TOP_GLOBAL + 0x03Cu)
#define NEO_TOP_IRQ_STATUS           (NEO_TOP_GLOBAL + 0x040u)   /* r, write 1 clears: bit 0 done, 1 err, 2 bridge, 3 lockstep, 4 command */
#define NEO_TOP_IRQ_MASK             (NEO_TOP_GLOBAL + 0x044u)   /* r/w: 1 masks (reset 0x1F) */
#define NEO_TOP_STATUS               (NEO_TOP_GLOBAL + 0x048u)   /* r: bit 0 irq, bit 1 err */
/* drop 0.43 */
#define NEO_TOP_PRIO                 (NEO_TOP_GLOBAL + 0x04Cu)   /* r/w: the bridge column routers' priority map */
#define NEO_TOP_LS_PAIR(k)           (NEO_TOP_GLOBAL + 0x050u + 4u * (uint32_t)(k))   /* r/w: bit 31 enable, [13:8] tile b, [5:0] tile a.
                                        Review finding 12 (drop 0.44): the word's fields cross into the mesh clock through independent
                                        synchronizers, so the enable may arrive a clk before the indices -- write the indices with
                                        enable 0 first, then the word with enable 1 (neo_ls_pair_set does); clear by writing 0 */
#define NEO_TOP_LS_ERR               (NEO_TOP_GLOBAL + 0x070u)   /* r: bit k pair k mismatched (cleared by clearing its enable) */
#define NEO_TOP_PWR_STATE(j)         (NEO_TOP_GLOBAL + 0x080u + 4u * (uint32_t)(j))   /* r: tile 8j + i in bits [4i +: 3] */
enum { NEO_PWR_ON, NEO_PWR_ISO, NEO_PWR_CLKOFF, NEO_PWR_RST, NEO_PWR_OFF, NEO_PWR_PWRON, NEO_PWR_RSTREL, NEO_PWR_CLKON };
#define NEO_TOP_CQ_CTRL              (NEO_TOP_GLOBAL + 0x0A0u)   /* r/w: bit 0 enable; write bit 1: reset the queue */
#define NEO_TOP_CQ_RING              (NEO_TOP_GLOBAL + 0x0A4u)   /* r/w: [9:0] word address in the command memory (4-word aligned), [19:16] log2 entries */
#define NEO_TOP_CQ_TAIL              (NEO_TOP_GLOBAL + 0x0A8u)   /* r/w: the doorbell */
#define NEO_TOP_CQ_HEAD              (NEO_TOP_GLOBAL + 0x0ACu)   /* r: the next command played */
#define NEO_TOP_CC_RING              (NEO_TOP_GLOBAL + 0x0B0u)   /* r/w: the completion ring, as CQ_RING */
#define NEO_TOP_CC_HEAD              (NEO_TOP_GLOBAL + 0x0B4u)   /* r/w: records taken by the host */
#define NEO_TOP_CC_TAIL              (NEO_TOP_GLOBAL + 0x0B8u)   /* r: records written */
#define NEO_TOP_CQ_STATUS            (NEO_TOP_GLOBAL + 0x0BCu)   /* r: bit 0 busy, [7:4] the player's state, bit 8 an error since reset */
#define NEO_TOP_TEL_CTRL             (NEO_TOP_GLOBAL + 0x0C0u)   /* w: bit 0 snapshot, bit 1 clear (the bridges' wait counters) */
#define NEO_TOP_TEL_CYCLES           (NEO_TOP_GLOBAL + 0x0C4u)   /* r: the snapshot's aclk cycles */
#define NEO_TOP_CMEM(w)              (NEO_TOP_GLOBAL + 0x1000u + 4u * (uint32_t)(w))  /* the command memory, 1,024 words */
/* A command (four words in the command ring): tile mask lo, tile mask hi, image {entries [31:16], no-wait [15], word address
 * [9:0]}, tag. An image entry (two words): {single [14], tile [13:8], register [7:0]}, value -- written into that register
 * of every tile of the mask (of the tile alone when single). A completion record (four words): tag, {entries played
 * [31:16], error [0]}, the global timer at completion (lo, hi). top_ctrl.sv has the rules. */
#define NEO_CQ_ENT(single, tile, reg) ((((uint32_t)(single) & 1u) << 14) | (((uint32_t)(tile) & 0x3Fu) << 8) | ((uint32_t)(reg) & 0xFFu))
#define NEO_CQ_IMG(entries, waddr, nowait) (((uint32_t)(entries) << 16) | ((uint32_t)(nowait) ? 0x8000u : 0u) | ((uint32_t)(waddr) & 0x3FFu))
#define NEO_TOP_BR(b, off)           (NEO_TOP_GLOBAL + 0x100u + 0x20u * (uint32_t)(b) + (uint32_t)(off))
#define NEO_TOP_BR_WIN_LO            0x0u                        /* window base bits 31:6 */
#define NEO_TOP_BR_WIN_HI            0x4u                        /* window base bits 39:32 */
#define NEO_TOP_BR_CTRL              0x8u                        /* bits 3:0: the host role's partition */
#define NEO_TOP_BR_ERR               0xCu                        /* r, write 1 clears: 0 crc, 1 lost, 2 axi, 3 timeout, 4 host; 5 parity (level) */
#define NEO_TOP_BR_RD_WAIT           0x10u                       /* r (drop 0.43): snapshot, AXI read wait cycles */
#define NEO_TOP_BR_WR_WAIT           0x14u                       /* r: snapshot, AXI write wait cycles */
#define NEO_TOP_BR_STATUS            0x18u                       /* r: bit 0 writes buffered or awaiting B */
#define NEO_TOP_IRQ_DONE             0x1u
#define NEO_TOP_IRQ_ERR              0x2u
#define NEO_TOP_IRQ_BRIDGE           0x4u
#define NEO_TOP_IRQ_LOCKSTEP         0x8u                        /* drop 0.43 */
#define NEO_TOP_IRQ_COMMAND          0x10u
/* A host bridge's AXI4 slave window: the tile index above the bank's byte offset (baw = log2 of the bank's words). A DMA
 * instruction addresses a bridge at x = NX (the bridge column), y = its row, a 20-bit word address in its 4 MB window at
 * the bridge's window base: byte = win_base + 4 * addr. */
#define NEO_TOP_HOST_ADDR(n, word, baw) (((uint64_t)(n) << ((baw) + 2)) | ((uint64_t)(word) << 2))

typedef struct { int x, y; } neo_tile_t;

/* A control-plane register access provided by the platform (AXI4-Lite byte addresses, NEO_TOP_*). */
typedef void (*neo_axil_wr_fn)(void *ctx, uint32_t addr, uint32_t val);
/* LS_PAIR k = (a, b): the indices first with the enable clear, then the enable (review finding 12). */
void neo_ls_pair_set(neo_axil_wr_fn wr, void *ctx, int k, int tile_a, int tile_b);

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
    uint32_t drain_fmt;                  /* drop 0.44: the INT8 drain's format in DRAIN_WR's base (NEO_DR_P: rows padded to entries) */
    int int4;                            /* drop 0.44: INT4 weight entries (WP4 words each, FETCH_W with arg 1) */
    int rbuf_words;                      /* drop 0.44: > 0 -- the owner fetches that many words of the residual rows into the */
    neo_tile_t rbuf_bank; uint32_t rbuf_addr;   /*   vector unit's residual buffer (FETCH_A with arg 1) before its GO */
} neo_group_t;
#define NEO_DR_T 0x8000u                 /* DRAIN_WR base (INT8, drop 0.43): [15] transpose, [14] entry, [13] pad, [12:0] group stride */
#define NEO_DR_E 0x4000u
#define NEO_DR_P 0x2000u

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
 * programs written to out[] (<= shares), -1 on error (no free tile, program memory full), -2 if an emitted
 * instruction is invalid (a zero-length fetch, drop 0.29), or -3 for a group of no output rows (cfg_m = 0: the
 * sequencer's drain would never end -- review finding 13, drop 0.44). */
int neo_compile_group(const neo_group_t *g, neo_tile_t owner, const neo_tile_t *free, int nfree, int shares,
                      neo_tile_prog_t *out);

/* Register access provided by the platform (memory-mapped or PCIe BAR). */
typedef void (*neo_wr_fn)(void *ctx, neo_tile_t tile, uint32_t reg, uint32_t val);
typedef uint32_t (*neo_rd_fn)(void *ctx, neo_tile_t tile, uint32_t reg);

/* Load a tile program through host_if and start it. */
void neo_load_and_start(const neo_tile_prog_t *tp, neo_wr_fn wr, void *ctx);
/* Poll STATUS until prog_done and !drain_busy; returns the status word. */
uint32_t neo_wait_done(neo_tile_t tile, neo_rd_fn rd, void *ctx, int max_polls);

/* Telemetry (drop 0.43): snapshot the tile's counters (and restart them when clear != 0), read the snapshot into t.
 * Returns NEO_TEL_NCNT, or -1 when the tile reports another number of counters. */
typedef struct { uint32_t c[NEO_TEL_NCNT]; } neo_telem_t;
int neo_telem_read(neo_tile_t tile, neo_wr_fn wr, neo_rd_fn rd, void *ctx, int clear, neo_telem_t *t);
/* MAC utilization of a snapshot in per mille (array valid cycles over the window's cycles; 0 for an empty window). */
uint32_t neo_telem_util_permille(const neo_telem_t *t);

/* Command queue (drop 0.43): the register image of tile programs -- per tile the NEO_NDESC descriptor registers, cfg_m
 * and the program (single-tile entries, as neo_load_and_start writes them), then `nextra` entries of extra[] (word pairs),
 * then CTRL start broadcast to the command's mask; tile index = y * nx + x. Writes word pairs into img and returns the
 * number of entries, -1 when more than max_entries. compiler/neo_backend.py's cq_image is the same image. */
int neo_cq_image(const neo_tile_prog_t *tp, int ntp, int nx, const uint32_t *extra, int nextra, uint32_t *img, int max_entries);
/* (-3 when a tile program carries cfg_m = 0, drop 0.44.) */

#endif
