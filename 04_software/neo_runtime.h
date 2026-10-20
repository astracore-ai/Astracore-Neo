/* neo_runtime.h -- the AstraCore Neo runtime over the chip top (drop 0.44, stream D3), on top of neo_host.h: a compiled
 * network's steps (compiler/neo_sched.py's image: per tile the descriptor, the requantizer's tables, the vector unit's
 * writes and the DMA program) loaded into the banks and the memory window through the host bridge and the host's memory,
 * issued as commands of the control plane's queue with doorbells, their completion records and interrupts polled, every
 * tile's telemetry snapshot and causes read per step, the results read back. Pure C99, no malloc, no I/O: the platform
 * supplies the accesses (neo_dev_t). The sequence is 04_software/neo_run.py's transaction for transaction
 * (02_dv/sim/check_c_runtime.py compares the two on recorded traces; the cocotb top suite's T9 / T10 replay it). */
#ifndef NEO_RUNTIME_H
#define NEO_RUNTIME_H
#include "neo_host.h"

#define NEO_RT_COLS_MAX 32            /* a core's columns (the requantizer's table rows per output tile) */
#define NEO_RT_WIN_MAX 520            /* register writes through a tile's window per step (two 256-entry LUTs and their addresses) */
#define NEO_RT_IMG_MAX 160            /* a tile's vector-unit entries in the image (SC, BI, K, the program and their addresses) */
#define NEO_RT_TILES_MAX 64
#define NEO_RT_CMEM_WORDS 1024        /* the command memory */
#define NEO_RT_CQ_LOG2 2              /* the command ring: 4 slots at word 0; the completion ring: 4 at word 16; images from 32 */
#define NEO_RT_CC_LOG2 2
#define NEO_RT_CC_BASE 16
#define NEO_RT_IMG_BASE 32
#define NEO_RT_IMG_ENTRIES ((NEO_RT_CMEM_WORDS - NEO_RT_IMG_BASE) / 2)
#define NEO_RT_MODE_QUEUE 0
#define NEO_RT_MODE_WINDOW 1

/* What a platform provides. `live`: reads return real values (a recording device answers 0 and sets live = 0, so no
 * value is checked). poll spins until (read & mask) == want and returns the value; host_rd / mem_rd fill `out`. */
typedef struct {
    void *ctx;
    int live;
    void (*axil_wr)(void *ctx, uint32_t addr, uint32_t val);
    uint32_t (*axil_rd)(void *ctx, uint32_t addr, const char *tag);
    uint32_t (*poll)(void *ctx, uint32_t addr, uint32_t mask, uint32_t want);
    void (*host_wr)(void *ctx, int node, uint32_t word, const uint32_t *w, int n);
    void (*host_rd)(void *ctx, int node, uint32_t word, uint32_t *out, int n, const char *name);
    void (*mem_wr)(void *ctx, uint32_t word, const uint32_t *w, int n);
    void (*mem_rd)(void *ctx, uint32_t word, uint32_t *out, int n, const char *name);
    void (*note)(void *ctx, const char *text);                    /* may be NULL */
} neo_dev_t;

/* One tile's part of a step (neo_sched.TileStep). win / img: (register, value) pairs; cfg in host_if's order. */
typedef struct {
    int node;
    uint16_t cfg[NEO_NDESC];
    uint32_t cfg_m;
    uint16_t mult[NEO_RT_COLS_MAX];
    uint8_t shift[NEO_RT_COLS_MAX];
    int8_t zp[NEO_RT_COLS_MAX];
    int relu;
    int nwin;
    uint32_t win[2 * NEO_RT_WIN_MAX];
    int nimg;
    uint32_t img[2 * NEO_RT_IMG_MAX];
    int nprog;
    uint64_t prog[NEO_MAX_PROG];
} neo_rt_tile_t;

typedef struct {
    const char *name;
    int ntiles;
    const neo_rt_tile_t *tiles;
} neo_rt_step_t;

typedef struct { uint32_t tag, status; uint64_t timer; } neo_rt_record_t;

typedef struct {
    neo_dev_t *dev;
    int nx, ny, rows, cols, nt;
    uint64_t win_base;
    int mode, telemetry;
    int step;                                   /* steps issued */
    neo_rt_record_t last;                       /* the last completion record (live devices) */
    neo_telem_t tel[NEO_RT_TILES_MAX];          /* the last step's snapshots per tile (telemetry on) */
    uint32_t cause[NEO_RT_TILES_MAX];           /* the last step's ERR_CAUSE per tile */
    char err[200];                              /* the text of the last error */
} neo_rt_t;

/* Errors: 0 ok; -1 the device's ID / CONFIG disagree with the image's geometry; -2 a step's image exceeds the command
 * memory; -3 a completion record with the error bit or a wrong tag / count; -4 a tile raised a cause (decoded in err);
 * -5 no command interrupt; -6 a read-back differs (neo_rt_check). */
int neo_rt_init(neo_rt_t *rt, neo_dev_t *dev, int nx, int ny, int rows, int cols, uint64_t win_base, int mode, int telemetry);
int neo_rt_load_mem(neo_rt_t *rt, uint32_t word, const uint32_t *w, int n);
int neo_rt_load_bank(neo_rt_t *rt, int node, uint32_t word, const uint32_t *w, int n);
int neo_rt_step(neo_rt_t *rt, const neo_rt_step_t *st);
void neo_rt_read_mem(neo_rt_t *rt, uint32_t word, uint32_t *out, int n, const char *name);
void neo_rt_read_bank(neo_rt_t *rt, int node, uint32_t word, uint32_t *out, int n, const char *name);
/* The command image of a step (neo_sched.entries_of): word pairs into img, returns the entries or -2 when over max. */
int neo_rt_image(const neo_rt_tile_t *tiles, int ntiles, int cols, uint32_t *img, int max_entries);
/* MAC utilization of a tile's last snapshot, per mille. */
uint32_t neo_rt_util_permille(const neo_rt_t *rt, int node);

#endif
