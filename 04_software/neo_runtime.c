/* neo_runtime.c -- see neo_runtime.h. The sequence of 04_software/neo_run.py (the Python model), transaction for transaction. */
#include "neo_runtime.h"

static void note(neo_rt_t *rt, const char *text) { if (rt->dev->note) rt->dev->note(rt->dev->ctx, text); }
static void wr(neo_rt_t *rt, uint32_t addr, uint32_t val) { rt->dev->axil_wr(rt->dev->ctx, addr, val); }
static uint32_t rd(neo_rt_t *rt, uint32_t addr, const char *tag) { return rt->dev->axil_rd(rt->dev->ctx, addr, tag); }
static uint32_t treg(int node, uint32_t reg) { return NEO_TOP_TILE_REG(node, reg); }

static int fail(neo_rt_t *rt, int code, const char *text) {
    int i = 0;
    while (text[i] && i < (int)sizeof(rt->err) - 1) { rt->err[i] = text[i]; i++; }
    rt->err[i] = 0;
    return code;
}

/* a short decimal / hex into a buffer (no stdio in the runtime) */
static int put_str(char *b, int pos, const char *s) { while (*s && pos < 190) b[pos++] = *s++; b[pos] = 0; return pos; }
static int put_num(char *b, int pos, uint32_t v, int hex) {
    char t[16]; int n = 0;
    do { uint32_t d = hex ? (v & 15u) : (v % 10u); t[n++] = (char)(d < 10 ? '0' + d : 'a' + d - 10); v = hex ? v >> 4 : v / 10u; } while (v);
    while (n && pos < 190) b[pos++] = t[--n];
    b[pos] = 0;
    return pos;
}

int neo_rt_init(neo_rt_t *rt, neo_dev_t *dev, int nx, int ny, int rows, int cols, uint64_t win_base, int mode, int telemetry) {
    rt->dev = dev; rt->nx = nx; rt->ny = ny; rt->rows = rows; rt->cols = cols; rt->nt = nx * ny;
    rt->win_base = win_base; rt->mode = mode; rt->telemetry = telemetry; rt->step = 0; rt->err[0] = 0;
    note(rt, "init");
    uint32_t id = rd(rt, NEO_TOP_ID, "id");
    if (dev->live && id != 0x4E454F54u) return fail(rt, -1, "ID is not 'NEOT'");
    rd(rt, NEO_TOP_VERSION, "version");
    uint32_t cfg = rd(rt, NEO_TOP_CONFIG, "config");
    if (dev->live) {
        int cnx = (int)(cfg & 0xFu), cny = (int)((cfg >> 4) & 0xFu), crows = (int)((cfg >> 16) & 0xFFu), ccols = (int)((cfg >> 24) & 0xFFu);
        if (cnx != nx || cny != ny || crows != rows || ccols != cols) return fail(rt, -1, "CONFIG disagrees with the image's geometry");
    }
    for (int n = 0; n < rt->nt; n++) wr(rt, treg(n, NEO_REG_CTRL), 2u);          /* every cause cleared */
    wr(rt, NEO_TOP_BR(0, NEO_TOP_BR_WIN_LO), (uint32_t)(win_base & 0xFFFFFFFFu));
    wr(rt, NEO_TOP_BR(0, NEO_TOP_BR_WIN_HI), (uint32_t)(win_base >> 32));
    wr(rt, NEO_TOP_IRQ_STATUS, 0x1Fu);
    if (mode == NEO_RT_MODE_QUEUE) {
        wr(rt, NEO_TOP_CQ_CTRL, 2u);                                                /* the queue reset */
        wr(rt, NEO_TOP_CQ_RING, ((uint32_t)NEO_RT_CQ_LOG2 << 16) | 0u);
        wr(rt, NEO_TOP_CC_RING, ((uint32_t)NEO_RT_CC_LOG2 << 16) | (uint32_t)NEO_RT_CC_BASE);
        wr(rt, NEO_TOP_IRQ_MASK, 0x0Fu);                                            /* the command cause unmasked */
        wr(rt, NEO_TOP_CQ_CTRL, 1u);
    }
    return 0;
}

int neo_rt_load_mem(neo_rt_t *rt, uint32_t word, const uint32_t *w, int n) {
    rt->dev->mem_wr(rt->dev->ctx, word, w, n);
    return 0;
}

int neo_rt_load_bank(neo_rt_t *rt, int node, uint32_t word, const uint32_t *w, int n) {
    uint32_t fence;
    rt->dev->host_wr(rt->dev->ctx, node, word, w, n);
    rt->dev->host_rd(rt->dev->ctx, node, word, &fence, 1, "fence");          /* the fence: a read after the writes on their path */
    return 0;
}

int neo_rt_image(const neo_rt_tile_t *tiles, int ntiles, int cols, uint32_t *img, int max_entries) {
    int n = 0;
#define PUT(e0, v) do { if (n >= max_entries) return -2; img[2 * n] = (e0); img[2 * n + 1] = (v); n++; } while (0)
    for (int i = 0; i < ntiles; i++) {
        const neo_rt_tile_t *t = &tiles[i];
        uint32_t one = (1u << 14) | ((uint32_t)t->node << 8);
        for (int j = 0; j < NEO_NDESC; j++) PUT(one | (uint32_t)(NEO_REG_CFG_BASE + j), (uint32_t)t->cfg[j]);
        PUT(one | NEO_REG_CFG_M, t->cfg_m);
        for (int c = 0; c < cols; c++) {
            PUT(one | NEO_REG_RQ_ADDR, (uint32_t)c);
            PUT(one | NEO_REG_RQ_TBL, (((uint32_t)t->zp[c] & 0xFFu) << 24) | ((uint32_t)t->shift[c] << 16) | (uint32_t)t->mult[c]);
        }
        PUT(one | NEO_REG_RQ_RELU, (uint32_t)t->relu);
        for (int j = 0; j < t->nimg; j++) PUT(one | (t->img[2 * j] & 0xFFu), t->img[2 * j + 1]);
        for (int j = 0; j < t->nprog; j++) {
            PUT(one | NEO_REG_PROG_ADDR, (uint32_t)j);
            PUT(one | NEO_REG_PROG_LO, (uint32_t)(t->prog[j] & 0xFFFFFFFFu));
            PUT(one | NEO_REG_PROG_HI, (uint32_t)(t->prog[j] >> 32));
        }
    }
    for (int i = 0; i < ntiles; i++) PUT((1u << 14) | ((uint32_t)tiles[i].node << 8) | NEO_REG_TEL_CTRL, 2u);   /* a fresh window */
    PUT(NEO_REG_CTRL, 1u);
#undef PUT
    return n;
}

int neo_rt_step(neo_rt_t *rt, const neo_rt_step_t *st) {
    static uint32_t img[2 * NEO_RT_IMG_ENTRIES];
    neo_dev_t *dev = rt->dev;
    int i = rt->step++;
    char text[200];
    int pos = put_str(text, 0, "step ");
    pos = put_num(text, pos, (uint32_t)i, 0);
    pos = put_str(text, pos, " ");
    pos = put_str(text, pos, st->name);
    pos = put_str(text, pos, ": tiles [");
    for (int k = 0; k < st->ntiles; k++) { if (k) pos = put_str(text, pos, ", "); pos = put_num(text, pos, (uint32_t)st->tiles[k].node, 0); }
    put_str(text, pos, "]");
    note(rt, text);
    for (int k = 0; k < st->ntiles; k++)
        for (int j = 0; j < st->tiles[k].nwin; j++) wr(rt, treg(st->tiles[k].node, st->tiles[k].win[2 * j]), st->tiles[k].win[2 * j + 1]);
    uint64_t mask = 0;
    for (int k = 0; k < st->ntiles; k++) mask |= (uint64_t)1 << st->tiles[k].node;
    if (rt->mode == NEO_RT_MODE_QUEUE) {
        int ne = neo_rt_image(st->tiles, st->ntiles, rt->cols, img, NEO_RT_IMG_ENTRIES);
        if (ne < 0) return fail(rt, -2, "the step's image exceeds the command memory");
        for (int j = 0; j < 2 * ne; j++) wr(rt, NEO_TOP_CMEM(NEO_RT_IMG_BASE + j), img[j]);
        uint32_t nq = 1u << NEO_RT_CQ_LOG2, slot = (uint32_t)i % nq;
        uint32_t cmd[4] = { (uint32_t)(mask & 0xFFFFFFFFu), (uint32_t)(mask >> 32), NEO_CQ_IMG(ne, NEO_RT_IMG_BASE, 0), (uint32_t)i + 1u };
        for (int k = 0; k < 4; k++) wr(rt, NEO_TOP_CMEM(4 * slot + (uint32_t)k), cmd[k]);
        wr(rt, NEO_TOP_CQ_TAIL, ((uint32_t)i + 1u) % nq);                                      /* the doorbell */
        dev->poll(dev->ctx, NEO_TOP_CC_TAIL, 0xFFu, ((uint32_t)i + 1u) % nq);                  /* the completion */
        uint32_t irq = rd(rt, NEO_TOP_IRQ_STATUS, "irq");
        if (dev->live && !(irq & NEO_TOP_IRQ_COMMAND)) return fail(rt, -5, "no command interrupt");
        wr(rt, NEO_TOP_IRQ_STATUS, NEO_TOP_IRQ_COMMAND);
        uint32_t rec[4];
        for (int k = 0; k < 4; k++) {
            char tag[24]; int p = put_str(tag, 0, "rec."); p = put_num(tag, p, (uint32_t)i, 0); p = put_str(tag, p, "."); put_num(tag, p, (uint32_t)k, 0);
            rec[k] = rd(rt, NEO_TOP_CMEM(NEO_RT_CC_BASE + 4 * slot + (uint32_t)k), tag);
        }
        if (dev->live) {
            if (rec[0] != (uint32_t)i + 1u) return fail(rt, -3, "completion record: wrong tag");
            if ((rec[1] >> 16) != (uint32_t)ne || (rec[1] & 1u)) return fail(rt, -3, "completion record: entries played or the error bit");
            rt->last.tag = rec[0]; rt->last.status = rec[1]; rt->last.timer = (uint64_t)rec[2] | ((uint64_t)rec[3] << 32);
        }
        wr(rt, NEO_TOP_CC_HEAD, ((uint32_t)i + 1u) % nq);
    } else {
        for (int k = 0; k < st->ntiles; k++) {
            int ne = neo_rt_image(&st->tiles[k], 1, rt->cols, img, NEO_RT_IMG_ENTRIES);
            if (ne < 0) return fail(rt, -2, "a tile's entries exceed the buffer");
            for (int j = 0; j < ne - 1; j++) wr(rt, treg(st->tiles[k].node, img[2 * j] & 0xFFu), img[2 * j + 1]);   /* no start */
        }
        for (int k = 0; k < st->ntiles; k++) wr(rt, treg(st->tiles[k].node, NEO_REG_CTRL), 1u);
        dev->poll(dev->ctx, NEO_TOP_DONE_LO, (uint32_t)(mask & 0xFFFFFFFFu), (uint32_t)(mask & 0xFFFFFFFFu));
    }
    for (int k = 0; k < st->ntiles; k++) {
        int node = st->tiles[k].node;
        if (rt->telemetry) {
            wr(rt, treg(node, NEO_REG_TEL_CTRL), 1u);                                           /* the window's snapshot */
            for (int c = 0; c < NEO_TEL_NCNT; c++) {
                char tag[32]; int p = put_str(tag, 0, "tel."); p = put_num(tag, p, (uint32_t)i, 0); p = put_str(tag, p, ".");
                p = put_num(tag, p, (uint32_t)node, 0); p = put_str(tag, p, "."); put_num(tag, p, (uint32_t)c, 0);
                rt->tel[node].c[c] = rd(rt, treg(node, (uint32_t)NEO_REG_TEL(c)), tag);
            }
        }
        char tag[32]; int p = put_str(tag, 0, "cause."); p = put_num(tag, p, (uint32_t)i, 0); p = put_str(tag, p, "."); put_num(tag, p, (uint32_t)node, 0);
        uint32_t cause = rd(rt, treg(node, NEO_REG_ERR_CAUSE), tag);
        rt->cause[node] = cause;
        if (dev->live && cause) {
            int q = put_str(text, 0, "tile "); q = put_num(text, q, (uint32_t)node, 0); q = put_str(text, q, " ERR_CAUSE 0x");
            q = put_num(text, q, cause, 1); q = put_str(text, q, ":");
            for (int b = 0; b <= NEO_CAUSE_NFLAGS; b++) if ((cause >> b) & 1u) { q = put_str(text, q, " "); q = put_str(text, q, neo_cause_name(b)); }
            return fail(rt, -4, text);
        }
    }
    return 0;
}

void neo_rt_read_mem(neo_rt_t *rt, uint32_t word, uint32_t *out, int n, const char *name) { rt->dev->mem_rd(rt->dev->ctx, word, out, n, name); }
void neo_rt_read_bank(neo_rt_t *rt, int node, uint32_t word, uint32_t *out, int n, const char *name) { rt->dev->host_rd(rt->dev->ctx, node, word, out, n, name); }

uint32_t neo_rt_util_permille(const neo_rt_t *rt, int node) { return neo_telem_util_permille(&rt->tel[node]); }
