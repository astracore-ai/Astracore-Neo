/* test_runtime.c -- runs the C runtime (neo_runtime.c) over a recording device on a network image (compiler/neo_sched.py's
 * text form) and prints every transaction in 04_software/neo_run.py's trace format; 02_dv/sim/check_c_runtime.py compares
 * the two traces word for word. Usage: test_runtime <image> [queue|window] [notel] */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "neo_runtime.h"

static void p_wr(void *c, uint32_t a, uint32_t v) { (void)c; printf("W %05x %08x\n", a, v); }
static uint32_t p_rd(void *c, uint32_t a, const char *tag) { (void)c; printf("R %05x %s\n", a, tag); return 0; }
static uint32_t p_poll(void *c, uint32_t a, uint32_t m, uint32_t w) { (void)c; printf("P %05x %08x %08x\n", a, m, w); return w; }
static void p_hw(void *c, int n, uint32_t w0, const uint32_t *w, int n_w) {
    (void)c; printf("H %d %u %d", n, w0, n_w); for (int i = 0; i < n_w; i++) printf(" %08x", w[i]); printf("\n"); }
static void p_hr(void *c, int n, uint32_t w0, uint32_t *o, int n_w, const char *name) {
    (void)c; printf("G %d %u %d %s\n", n, w0, n_w, name); for (int i = 0; i < n_w; i++) o[i] = 0; }
static void p_mw(void *c, uint32_t w0, const uint32_t *w, int n_w) {
    (void)c; printf("M %u %d", w0, n_w); for (int i = 0; i < n_w; i++) printf(" %08x", w[i]); printf("\n"); }
static void p_mr(void *c, uint32_t w0, uint32_t *o, int n_w, const char *name) {
    (void)c; printf("N %u %d %s\n", w0, n_w, name); for (int i = 0; i < n_w; i++) o[i] = 0; }
static void p_note(void *c, const char *t) { (void)c; printf("# %s\n", t); }

static char tok[64];
static FILE *fin;
static const char *next(void) { if (fscanf(fin, "%63s", tok) != 1) { fprintf(stderr, "image ends early\n"); exit(2); } return tok; }
static uint64_t next_hex(void) { return strtoull(next(), NULL, 16); }
static int next_int(void) { return (int)strtol(next(), NULL, 10); }
static void expect(const char *s) { if (strcmp(next(), s)) { fprintf(stderr, "expected %s, got %s\n", s, tok); exit(2); } }

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: test_runtime <image> [queue|window] [notel]\n"); return 2; }
    fin = fopen(argv[1], "r");
    if (!fin) { perror(argv[1]); return 2; }
    int mode = (argc > 2 && !strcmp(argv[2], "window")) ? NEO_RT_MODE_WINDOW : NEO_RT_MODE_QUEUE;
    int tel = !(argc > 3 && !strcmp(argv[3], "notel"));
    expect("NEOIMG"); expect("1"); expect("GEO");
    int nx = next_int(), ny = next_int(), rows = next_int(), cols = next_int();
    for (int i = 0; i < 6; i++) next_int();                               /* nchk, acc_rows, abuf, wbuf, bank, wpf */
    neo_dev_t dev = { NULL, 0, p_wr, p_rd, p_poll, p_hw, p_hr, p_mw, p_mr, p_note };
    static neo_rt_t rt;
    int r = neo_rt_init(&rt, &dev, nx, ny, rows, cols, 0xABCDEF0040ull, mode, tel);
    if (r) { fprintf(stderr, "init: %d %s\n", r, rt.err); return 1; }
    static neo_rt_tile_t tiles[NEO_RT_TILES_MAX];
    static char names[NEO_RT_TILES_MAX][64];
    uint32_t *buf = malloc(sizeof(uint32_t) << 20);
    static char nbuf[64], hbuf[64];
    for (;;) {
        const char *key = next();
        if (!strcmp(key, "END")) break;
        if (!strcmp(key, "LOAD")) {
            strncpy(hbuf, next(), 63); uint32_t addr = (uint32_t)next_int(); int n = next_int(); strncpy(nbuf, next(), 63);
            char text[160]; snprintf(text, sizeof text, "load %s: %d words into %s at %u", nbuf, n, hbuf, addr); p_note(NULL, text);
            for (int i = 0; i < n; i++) buf[i] = (uint32_t)next_hex();
            if (!strcmp(hbuf, "mem")) neo_rt_load_mem(&rt, addr, buf, n);
            else neo_rt_load_bank(&rt, atoi(hbuf + 4), addr, buf, n);
        } else if (!strcmp(key, "STEP")) {
            strncpy(names[0], next(), 63); next();                                /* the layer name */
            int ntl = next_int();
            for (int k = 0; k < ntl; k++) {
                neo_rt_tile_t *t = &tiles[k];
                expect("TILE"); t->node = next_int(); next(); t->cfg_m = (uint32_t)next_int(); t->relu = next_int();
                expect("CFG"); for (int j = 0; j < NEO_NDESC; j++) t->cfg[j] = (uint16_t)next_int();
                expect("RQ"); for (int c = 0; c < cols; c++) { t->mult[c] = (uint16_t)next_int(); t->shift[c] = (uint8_t)next_int(); t->zp[c] = (int8_t)next_int(); }
                expect("WIN"); t->nwin = next_int(); for (int j = 0; j < 2 * t->nwin; j++) t->win[j] = (uint32_t)next_hex();
                expect("IMG"); t->nimg = next_int(); for (int j = 0; j < 2 * t->nimg; j++) t->img[j] = (uint32_t)next_hex();
                expect("PROG"); t->nprog = next_int(); for (int j = 0; j < t->nprog; j++) t->prog[j] = next_hex();
            }
            neo_rt_step_t st = { names[0], ntl, tiles };
            r = neo_rt_step(&rt, &st);
            if (r) { fprintf(stderr, "step %s: %d %s\n", names[0], r, rt.err); return 1; }
        } else if (!strcmp(key, "READ")) {
            strncpy(hbuf, next(), 63); uint32_t addr = (uint32_t)next_int(); int n = next_int(); strncpy(nbuf, next(), 63);
            for (int i = 0; i < n; i++) next_hex();                               /* the expected words: the live check is the platform's */
            if (!strcmp(hbuf, "mem")) neo_rt_read_mem(&rt, addr, buf, n, nbuf);
            else neo_rt_read_bank(&rt, atoi(hbuf + 4), addr, buf, n, nbuf);
        } else { fprintf(stderr, "image token %s\n", key); return 2; }
    }
    free(buf);
    return 0;
}
