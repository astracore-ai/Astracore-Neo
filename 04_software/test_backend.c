/* test_backend.c -- prints the programs the C driver emits for the M6 configuration, for comparison
 * with compiler/neo_backend.py (sim/check_c_backend.py does the comparison). */
#include <stdio.h>
#include "neo_host.h"
int main(void) {
    neo_group_t g = { .h = 6, .w = 6, .cin = 40, .cout = 7, .k = 3, .s = 1, .p = 1, .rows = 16, .cols = 8, .nx = 2, .ny = 2,
                      .act_bank = {1, 1}, .act_base = 0, .w_bank = {1, 1}, .w_base = 512, .nt = 0, .oy0 = 0, .oy_n = 6 };
    neo_tile_t owner = {0, 0};
    neo_tile_t free[3] = {{1, 0}, {0, 1}, {1, 1}};
    neo_tile_prog_t out[4];
    neo_group_t g0 = g;                  /* the first case, kept for the command queue's image below */
    int n = neo_compile_group(&g, owner, free, 3, 3, out);
    printf("tiles %d\n", n);
    for (int i = 0; i < n; i++) {
        printf("tile %d %d role %d cfg_m %u cfg", out[i].tile.x, out[i].tile.y, out[i].role, out[i].cfg_m);
        for (int j = 0; j < NEO_NDESC; j++) printf(" %u", out[i].cfg[j]);
        printf("\nprog");
        for (int j = 0; j < out[i].nprog; j++) printf(" %016llx", (unsigned long long)out[i].prog[j]);
        printf("\n");
    }
    /* second case: output-row range 3..5 (M7) */
    g.oy0 = 3; g.oy_n = 3; neo_tile_t owner2 = {1, 0}; neo_tile_t free2[3] = {{0, 0}, {0, 1}, {1, 1}};
    n = neo_compile_group(&g, owner2, free2, 3, 1, out);
    printf("tiles %d\n", n);
    for (int i = 0; i < n; i++) {
        printf("tile %d %d role %d cfg_m %u cfg", out[i].tile.x, out[i].tile.y, out[i].role, out[i].cfg_m);
        for (int j = 0; j < NEO_NDESC; j++) printf(" %u", out[i].cfg[j]);
        printf("\nprog");
        for (int j = 0; j < out[i].nprog; j++) printf(" %016llx", (unsigned long long)out[i].prog[j]);
        printf("\n");
    }
    /* the command queue's image of the first case (drop 0.43): its three tile programs, one extra entry, the start */
    {
        neo_tile_prog_t c1[4];
        int n1 = neo_compile_group(&g0, owner, free, 3, 3, c1);
        static uint32_t img[2 * 256];
        uint32_t extra[2] = { NEO_CQ_ENT(1, 0, NEO_REG_RQ_RELU), 1u };
        int ne = neo_cq_image(c1, n1, 2, extra, 1, img, 256);
        printf("cqimage %d", ne);
        for (int j = 0; j < 2 * ne; j++) printf(" %08x", img[j]);
        printf("\n");
    }
    /* third case (drop 0.42): a 2:4 sparse layer on the sparse-capable core (64 input channels = two 32-channel tiles at
     * 16 rows), K-split over two tiles (M20's configuration) */
    neo_group_t g3 = { .h = 6, .w = 6, .cin = 64, .cout = 7, .k = 3, .s = 1, .p = 1, .rows = 16, .cols = 8, .nx = 2, .ny = 2,
                       .act_bank = {1, 1}, .act_base = 0, .w_bank = {1, 1}, .w_base = 512, .nt = 0, .oy0 = 0, .oy_n = 6,
                       .nchk = 4, .sparse = 1 };
    n = neo_compile_group(&g3, owner, free, 3, 2, out);
    printf("tiles %d\n", n);
    for (int i = 0; i < n; i++) {
        printf("tile %d %d role %d cfg_m %u cfg", out[i].tile.x, out[i].tile.y, out[i].role, out[i].cfg_m);
        for (int j = 0; j < NEO_NDESC; j++) printf(" %u", out[i].cfg[j]);
        printf("\nprog");
        for (int j = 0; j < out[i].nprog; j++) printf(" %016llx", (unsigned long long)out[i].prog[j]);
        printf("\n");
    }
    return 0;
}
