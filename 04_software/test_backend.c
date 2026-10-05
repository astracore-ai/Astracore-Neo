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
    return 0;
}
