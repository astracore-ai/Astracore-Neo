# Compiler tool-qualification kit report (2026-10-07, drop 0.33)

130/130 cases passed in 3.2 s.

**Tool confidence argument.** TI1/TD1 -> TCL1: every compiled network is verified bit-exact against the reference model (lowering on the cycle-accurate reference, programs on the RTL mesh in the regressions), so a tool error cannot reach silicon undetected; the compiler itself therefore needs no further qualification beyond this kit and the project's configuration management (ISO 26262-8 11.4.5).

| Case | Kind | Parameters | Result | Detail |
| --- | --- | --- | --- | --- |
| TQ1-000 | lowering value-exact | h=9 w=11 cin=3 cout=6 k=3 s=2 p=1 | PASS | M=30 K=27 N=6 tiles 1x1 of 32x32 |
| TQ1-001 | lowering value-exact | h=11 w=4 cin=55 cout=14 k=5 s=1 p=2 | PASS | M=44 K=1375 N=14 tiles 43x1 of 32x16 |
| TQ1-002 | lowering value-exact | h=11 w=3 cin=31 cout=39 k=3 s=1 p=1 | PASS | M=33 K=279 N=39 tiles 9x2 of 32x32 |
| TQ1-003 | lowering value-exact | h=4 w=5 cin=6 cout=25 k=5 s=2 p=2 | PASS | M=6 K=150 N=25 tiles 10x1 of 16x32 |
| TQ1-004 | lowering value-exact | h=11 w=10 cin=43 cout=21 k=5 s=1 p=2 | PASS | M=110 K=1075 N=21 tiles 135x1 of 8x32 |
| TQ1-005 | lowering value-exact | h=6 w=10 cin=55 cout=29 k=5 s=1 p=2 | PASS | M=60 K=1375 N=29 tiles 43x2 of 32x16 |
| TQ1-006 | lowering value-exact | h=10 w=8 cin=48 cout=29 k=3 s=1 p=1 | PASS | M=80 K=432 N=29 tiles 27x4 of 16x8 |
| TQ1-007 | lowering value-exact | h=7 w=8 cin=55 cout=31 k=5 s=2 p=2 | PASS | M=16 K=1375 N=31 tiles 172x1 of 8x32 |
| TQ1-008 | lowering value-exact | h=4 w=3 cin=32 cout=1 k=5 s=2 p=2 | PASS | M=4 K=800 N=1 tiles 50x1 of 16x8 |
| TQ1-009 | lowering value-exact | h=8 w=6 cin=44 cout=33 k=3 s=1 p=1 | PASS | M=48 K=396 N=33 tiles 25x2 of 16x32 |
| TQ1-010 | lowering value-exact | h=3 w=10 cin=16 cout=12 k=1 s=2 p=0 | PASS | M=10 K=16 N=12 tiles 1x2 of 32x8 |
| TQ1-011 | lowering value-exact | h=6 w=4 cin=2 cout=16 k=5 s=2 p=2 | PASS | M=6 K=50 N=16 tiles 4x1 of 16x32 |
| TQ1-012 | lowering value-exact | h=7 w=8 cin=22 cout=32 k=5 s=2 p=2 | PASS | M=16 K=550 N=32 tiles 18x4 of 32x8 |
| TQ1-013 | lowering value-exact | h=7 w=7 cin=33 cout=29 k=1 s=1 p=0 | PASS | M=49 K=33 N=29 tiles 3x2 of 16x16 |
| TQ1-014 | lowering value-exact | h=6 w=5 cin=1 cout=16 k=1 s=2 p=0 | PASS | M=9 K=1 N=16 tiles 1x1 of 8x32 |
| TQ1-015 | lowering value-exact | h=9 w=7 cin=32 cout=8 k=5 s=1 p=2 | PASS | M=63 K=800 N=8 tiles 25x1 of 32x32 |
| TQ1-016 | lowering value-exact | h=9 w=7 cin=9 cout=2 k=5 s=1 p=2 | PASS | M=63 K=225 N=2 tiles 29x1 of 8x16 |
| TQ1-017 | lowering value-exact | h=10 w=11 cin=44 cout=26 k=1 s=1 p=0 | PASS | M=110 K=44 N=26 tiles 2x4 of 32x8 |
| TQ1-018 | lowering value-exact | h=8 w=5 cin=40 cout=5 k=3 s=2 p=1 | PASS | M=12 K=360 N=5 tiles 23x1 of 16x8 |
| TQ1-019 | lowering value-exact | h=6 w=4 cin=45 cout=21 k=3 s=2 p=1 | PASS | M=6 K=405 N=21 tiles 51x2 of 8x16 |
| TQ1-020 | lowering value-exact | h=7 w=6 cin=5 cout=8 k=5 s=2 p=2 | PASS | M=12 K=125 N=8 tiles 16x1 of 8x16 |
| TQ1-021 | lowering value-exact | h=6 w=6 cin=55 cout=4 k=5 s=1 p=2 | PASS | M=36 K=1375 N=4 tiles 172x1 of 8x8 |
| TQ1-022 | lowering value-exact | h=3 w=8 cin=20 cout=29 k=1 s=2 p=0 | PASS | M=8 K=20 N=29 tiles 1x4 of 32x8 |
| TQ1-023 | lowering value-exact | h=3 w=8 cin=18 cout=39 k=1 s=2 p=0 | PASS | M=8 K=18 N=39 tiles 1x5 of 32x8 |
| TQ1-024 | lowering value-exact | h=6 w=6 cin=37 cout=5 k=3 s=2 p=1 | PASS | M=9 K=333 N=5 tiles 21x1 of 16x32 |
| TQ1-025 | lowering value-exact | h=6 w=10 cin=26 cout=29 k=1 s=2 p=0 | PASS | M=15 K=26 N=29 tiles 2x2 of 16x16 |
| TQ1-026 | lowering value-exact | h=11 w=6 cin=50 cout=26 k=5 s=1 p=2 | PASS | M=66 K=1250 N=26 tiles 40x1 of 32x32 |
| TQ1-027 | lowering value-exact | h=10 w=9 cin=26 cout=29 k=1 s=1 p=0 | PASS | M=90 K=26 N=29 tiles 1x4 of 32x8 |
| TQ1-028 | lowering value-exact | h=6 w=3 cin=18 cout=10 k=3 s=2 p=1 | PASS | M=6 K=162 N=10 tiles 21x1 of 8x16 |
| TQ1-029 | lowering value-exact | h=10 w=7 cin=8 cout=9 k=1 s=2 p=0 | PASS | M=20 K=8 N=9 tiles 1x1 of 32x16 |
| TQ1-030 | lowering value-exact | h=4 w=5 cin=6 cout=27 k=5 s=1 p=2 | PASS | M=20 K=150 N=27 tiles 5x4 of 32x8 |
| TQ1-031 | lowering value-exact | h=8 w=11 cin=12 cout=17 k=3 s=2 p=1 | PASS | M=24 K=108 N=17 tiles 14x1 of 8x32 |
| TQ1-032 | lowering value-exact | h=11 w=4 cin=50 cout=27 k=3 s=1 p=1 | PASS | M=44 K=450 N=27 tiles 15x2 of 32x16 |
| TQ1-033 | lowering value-exact | h=3 w=7 cin=47 cout=8 k=1 s=2 p=0 | PASS | M=8 K=47 N=8 tiles 6x1 of 8x32 |
| TQ1-034 | lowering value-exact | h=7 w=5 cin=49 cout=23 k=5 s=1 p=2 | PASS | M=35 K=1225 N=23 tiles 39x1 of 32x32 |
| TQ1-035 | lowering value-exact | h=5 w=6 cin=65 cout=3 k=3 s=2 p=1 | PASS | M=9 K=585 N=3 tiles 74x1 of 8x8 |
| TQ1-036 | lowering value-exact | h=6 w=7 cin=48 cout=29 k=5 s=1 p=2 | PASS | M=42 K=1200 N=29 tiles 150x4 of 8x8 |
| TQ1-037 | lowering value-exact | h=6 w=8 cin=62 cout=15 k=5 s=2 p=2 | PASS | M=12 K=1550 N=15 tiles 49x1 of 32x32 |
| TQ1-038 | lowering value-exact | h=11 w=7 cin=43 cout=22 k=5 s=2 p=2 | PASS | M=24 K=1075 N=22 tiles 135x3 of 8x8 |
| TQ1-039 | lowering value-exact | h=8 w=7 cin=7 cout=26 k=1 s=1 p=0 | PASS | M=56 K=7 N=26 tiles 1x4 of 16x8 |
| TQ1-040 | lowering value-exact | h=10 w=7 cin=19 cout=32 k=5 s=2 p=2 | PASS | M=20 K=475 N=32 tiles 60x4 of 8x8 |
| TQ1-041 | lowering value-exact | h=3 w=7 cin=19 cout=12 k=5 s=2 p=2 | PASS | M=8 K=475 N=12 tiles 60x1 of 8x16 |
| TQ1-042 | lowering value-exact | h=3 w=10 cin=60 cout=34 k=1 s=2 p=0 | PASS | M=10 K=60 N=34 tiles 8x3 of 8x16 |
| TQ1-043 | lowering value-exact | h=7 w=5 cin=50 cout=31 k=1 s=2 p=0 | PASS | M=12 K=50 N=31 tiles 2x4 of 32x8 |
| TQ1-044 | lowering value-exact | h=4 w=4 cin=26 cout=23 k=5 s=2 p=2 | PASS | M=4 K=650 N=23 tiles 82x2 of 8x16 |
| TQ1-045 | lowering value-exact | h=6 w=6 cin=26 cout=24 k=3 s=1 p=1 | PASS | M=36 K=234 N=24 tiles 30x3 of 8x8 |
| TQ1-046 | lowering value-exact | h=8 w=9 cin=63 cout=32 k=3 s=2 p=1 | PASS | M=20 K=567 N=32 tiles 71x4 of 8x8 |
| TQ1-047 | lowering value-exact | h=7 w=5 cin=10 cout=5 k=3 s=2 p=1 | PASS | M=12 K=90 N=5 tiles 12x1 of 8x8 |
| TQ1-048 | lowering value-exact | h=5 w=7 cin=68 cout=7 k=5 s=2 p=2 | PASS | M=12 K=1700 N=7 tiles 213x1 of 8x32 |
| TQ1-049 | lowering value-exact | h=5 w=10 cin=48 cout=6 k=5 s=2 p=2 | PASS | M=15 K=1200 N=6 tiles 38x1 of 32x16 |
| TQ1-050 | lowering value-exact | h=10 w=11 cin=10 cout=6 k=1 s=2 p=0 | PASS | M=30 K=10 N=6 tiles 2x1 of 8x16 |
| TQ1-051 | lowering value-exact | h=3 w=5 cin=16 cout=39 k=1 s=2 p=0 | PASS | M=6 K=16 N=39 tiles 2x3 of 8x16 |
| TQ1-052 | lowering value-exact | h=7 w=10 cin=46 cout=38 k=5 s=1 p=2 | PASS | M=70 K=1150 N=38 tiles 72x2 of 16x32 |
| TQ1-053 | lowering value-exact | h=7 w=9 cin=29 cout=38 k=3 s=1 p=1 | PASS | M=63 K=261 N=38 tiles 17x5 of 16x8 |
| TQ1-054 | lowering value-exact | h=10 w=5 cin=58 cout=10 k=3 s=1 p=1 | PASS | M=50 K=522 N=10 tiles 17x1 of 32x16 |
| TQ1-055 | lowering value-exact | h=8 w=3 cin=65 cout=27 k=1 s=1 p=0 | PASS | M=24 K=65 N=27 tiles 9x2 of 8x16 |
| TQ1-056 | lowering value-exact | h=3 w=7 cin=20 cout=29 k=3 s=1 p=1 | PASS | M=21 K=180 N=29 tiles 23x2 of 8x16 |
| TQ1-057 | lowering value-exact | h=5 w=5 cin=51 cout=10 k=5 s=1 p=2 | PASS | M=25 K=1275 N=10 tiles 80x2 of 16x8 |
| TQ1-058 | lowering value-exact | h=3 w=7 cin=6 cout=25 k=5 s=1 p=2 | PASS | M=21 K=150 N=25 tiles 5x4 of 32x8 |
| TQ1-059 | lowering value-exact | h=5 w=6 cin=37 cout=20 k=5 s=1 p=2 | PASS | M=30 K=925 N=20 tiles 58x2 of 16x16 |
| TQ2-000 | program emission structure | h=39 w=23 cin=179 cout=42 k=1 s=1 p=0 mesh=8 shares=3 oy0=9 oy_n=23 | PASS | 3 tile programs |
| TQ2-001 | program emission structure | h=21 w=38 cin=109 cout=17 k=1 s=1 p=0 mesh=2 shares=2 oy0=2 oy_n=19 | PASS | 2 tile programs |
| TQ2-002 | program emission structure | h=28 w=39 cin=105 cout=41 k=1 s=2 p=0 mesh=2 shares=1 oy0=8 oy_n=5 | PASS | 1 tile programs |
| TQ2-003 | program emission structure | h=12 w=30 cin=96 cout=63 k=3 s=1 p=1 mesh=8 shares=2 oy0=0 oy_n=12 | PASS | 2 tile programs |
| TQ2-004 | program emission structure | h=7 w=35 cin=29 cout=42 k=1 s=1 p=0 mesh=2 shares=3 oy0=0 oy_n=6 | PASS | 1 tile programs |
| TQ2-005 | program emission structure | h=7 w=8 cin=171 cout=1 k=3 s=1 p=1 mesh=8 shares=1 oy0=1 oy_n=4 | PASS | 1 tile programs |
| TQ2-006 | program emission structure | h=28 w=4 cin=131 cout=40 k=3 s=2 p=1 mesh=8 shares=1 oy0=5 oy_n=6 | PASS | 1 tile programs |
| TQ2-007 | program emission structure | h=9 w=10 cin=190 cout=36 k=3 s=1 p=1 mesh=8 shares=4 oy0=0 oy_n=6 | PASS | 4 tile programs |
| TQ2-008 | program emission structure | h=35 w=11 cin=81 cout=23 k=1 s=2 p=0 mesh=2 shares=1 oy0=2 oy_n=15 | PASS | 1 tile programs |
| TQ2-009 | program emission structure | h=14 w=33 cin=49 cout=59 k=3 s=2 p=1 mesh=2 shares=1 oy0=4 oy_n=2 | PASS | 1 tile programs |
| TQ2-010 | program emission structure | h=39 w=37 cin=194 cout=17 k=1 s=1 p=0 mesh=8 shares=1 oy0=4 oy_n=27 | PASS | 1 tile programs |
| TQ2-011 | program emission structure | h=12 w=38 cin=5 cout=54 k=3 s=1 p=1 mesh=2 shares=2 oy0=5 oy_n=4 | PASS | 2 tile programs |
| TQ2-012 | program emission structure | h=5 w=22 cin=196 cout=24 k=1 s=2 p=0 mesh=2 shares=4 oy0=0 oy_n=2 | PASS | 4 tile programs |
| TQ2-013 | program emission structure | h=26 w=5 cin=52 cout=10 k=3 s=2 p=1 mesh=2 shares=1 oy0=4 oy_n=8 | PASS | 1 tile programs |
| TQ2-014 | program emission structure | h=28 w=31 cin=162 cout=22 k=1 s=1 p=0 mesh=8 shares=3 oy0=6 oy_n=20 | PASS | 3 tile programs |
| TQ2-015 | program emission structure | h=36 w=19 cin=60 cout=8 k=1 s=1 p=0 mesh=8 shares=3 oy0=12 oy_n=10 | PASS | 2 tile programs |
| TQ2-016 | program emission structure | h=39 w=34 cin=149 cout=41 k=1 s=2 p=0 mesh=2 shares=2 oy0=10 oy_n=10 | PASS | 2 tile programs |
| TQ2-017 | program emission structure | h=10 w=36 cin=57 cout=62 k=1 s=2 p=0 mesh=8 shares=3 oy0=0 oy_n=4 | PASS | 2 tile programs |
| TQ2-018 | program emission structure | h=26 w=34 cin=152 cout=18 k=3 s=1 p=1 mesh=8 shares=4 oy0=9 oy_n=15 | PASS | 4 tile programs |
| TQ2-019 | program emission structure | h=26 w=10 cin=113 cout=4 k=3 s=2 p=1 mesh=8 shares=3 oy0=6 oy_n=3 | PASS | 3 tile programs |
| TQ2-020 | program emission structure | h=15 w=16 cin=194 cout=48 k=1 s=2 p=0 mesh=8 shares=3 oy0=0 oy_n=8 | PASS | 3 tile programs |
| TQ2-021 | program emission structure | h=9 w=7 cin=185 cout=19 k=1 s=2 p=0 mesh=2 shares=3 oy0=1 oy_n=4 | PASS | 3 tile programs |
| TQ2-022 | program emission structure | h=29 w=23 cin=46 cout=40 k=3 s=2 p=1 mesh=2 shares=1 oy0=9 oy_n=4 | PASS | 1 tile programs |
| TQ2-023 | program emission structure | h=22 w=16 cin=63 cout=24 k=3 s=1 p=1 mesh=2 shares=3 oy0=7 oy_n=10 | PASS | 3 tile programs |
| TQ2-024 | program emission structure | h=18 w=30 cin=58 cout=7 k=1 s=2 p=0 mesh=2 shares=4 oy0=6 oy_n=2 | PASS | 2 tile programs |
| TQ2-025 | program emission structure | h=31 w=18 cin=31 cout=56 k=3 s=1 p=1 mesh=8 shares=2 oy0=3 oy_n=1 | PASS | 2 tile programs |
| TQ2-026 | program emission structure | h=16 w=20 cin=31 cout=60 k=1 s=2 p=0 mesh=8 shares=3 oy0=0 oy_n=8 | PASS | 1 tile programs |
| TQ2-027 | program emission structure | h=26 w=35 cin=189 cout=5 k=3 s=2 p=1 mesh=2 shares=4 oy0=10 oy_n=1 | PASS | 4 tile programs |
| TQ2-028 | program emission structure | h=35 w=23 cin=46 cout=42 k=1 s=2 p=0 mesh=8 shares=2 oy0=5 oy_n=4 | PASS | 2 tile programs |
| TQ2-029 | program emission structure | h=11 w=9 cin=188 cout=32 k=1 s=2 p=0 mesh=8 shares=3 oy0=0 oy_n=6 | PASS | 3 tile programs |
| TQ2-030 | program emission structure | h=19 w=20 cin=113 cout=20 k=3 s=1 p=1 mesh=8 shares=3 oy0=0 oy_n=12 | PASS | 3 tile programs |
| TQ2-031 | program emission structure | h=24 w=15 cin=127 cout=51 k=3 s=2 p=1 mesh=8 shares=4 oy0=2 oy_n=1 | PASS | 4 tile programs |
| TQ2-032 | program emission structure | h=30 w=18 cin=66 cout=37 k=3 s=1 p=1 mesh=8 shares=4 oy0=20 oy_n=8 | PASS | 4 tile programs |
| TQ2-033 | program emission structure | h=34 w=35 cin=48 cout=45 k=1 s=2 p=0 mesh=2 shares=3 oy0=4 oy_n=6 | PASS | 2 tile programs |
| TQ2-034 | program emission structure | h=28 w=38 cin=53 cout=30 k=1 s=2 p=0 mesh=8 shares=1 oy0=8 oy_n=3 | PASS | 1 tile programs |
| TQ2-035 | program emission structure | h=34 w=20 cin=18 cout=60 k=1 s=2 p=0 mesh=2 shares=3 oy0=2 oy_n=7 | PASS | 1 tile programs |
| TQ2-036 | program emission structure | h=4 w=33 cin=160 cout=46 k=3 s=2 p=1 mesh=2 shares=1 oy0=0 oy_n=2 | PASS | 1 tile programs |
| TQ2-037 | program emission structure | h=27 w=37 cin=188 cout=6 k=1 s=2 p=0 mesh=2 shares=4 oy0=4 oy_n=5 | PASS | 4 tile programs |
| TQ2-038 | program emission structure | h=30 w=12 cin=23 cout=63 k=1 s=2 p=0 mesh=8 shares=2 oy0=0 oy_n=7 | PASS | 1 tile programs |
| TQ2-039 | program emission structure | h=29 w=36 cin=60 cout=37 k=1 s=2 p=0 mesh=8 shares=3 oy0=2 oy_n=1 | PASS | 2 tile programs |
| TQ2-040 | program emission structure | h=29 w=12 cin=149 cout=26 k=1 s=1 p=0 mesh=8 shares=4 oy0=13 oy_n=6 | PASS | 4 tile programs |
| TQ2-041 | program emission structure | h=27 w=28 cin=144 cout=22 k=3 s=2 p=1 mesh=2 shares=1 oy0=0 oy_n=14 | PASS | 1 tile programs |
| TQ2-042 | program emission structure | h=30 w=17 cin=68 cout=16 k=3 s=1 p=1 mesh=8 shares=4 oy0=0 oy_n=26 | PASS | 4 tile programs |
| TQ2-043 | program emission structure | h=17 w=15 cin=132 cout=40 k=1 s=2 p=0 mesh=8 shares=4 oy0=1 oy_n=7 | PASS | 4 tile programs |
| TQ2-044 | program emission structure | h=31 w=14 cin=72 cout=13 k=1 s=2 p=0 mesh=2 shares=3 oy0=0 oy_n=14 | PASS | 3 tile programs |
| TQ2-045 | program emission structure | h=4 w=30 cin=25 cout=48 k=1 s=2 p=0 mesh=8 shares=3 oy0=0 oy_n=1 | PASS | 1 tile programs |
| TQ2-046 | program emission structure | h=25 w=8 cin=84 cout=53 k=1 s=2 p=0 mesh=2 shares=1 oy0=2 oy_n=11 | PASS | 1 tile programs |
| TQ2-047 | program emission structure | h=4 w=31 cin=34 cout=32 k=3 s=1 p=1 mesh=8 shares=1 oy0=0 oy_n=4 | PASS | 1 tile programs |
| TQ2-048 | program emission structure | h=4 w=14 cin=55 cout=20 k=1 s=2 p=0 mesh=8 shares=2 oy0=0 oy_n=2 | PASS | 2 tile programs |
| TQ2-049 | program emission structure | h=9 w=15 cin=196 cout=25 k=1 s=1 p=0 mesh=2 shares=3 oy0=0 oy_n=1 | PASS | 3 tile programs |
| TQ2-050 | program emission structure | h=13 w=19 cin=147 cout=2 k=3 s=2 p=1 mesh=2 shares=3 oy0=0 oy_n=7 | PASS | 3 tile programs |
| TQ2-051 | program emission structure | h=32 w=11 cin=101 cout=50 k=3 s=2 p=1 mesh=8 shares=2 oy0=4 oy_n=12 | PASS | 2 tile programs |
| TQ2-052 | program emission structure | h=38 w=38 cin=71 cout=46 k=3 s=2 p=1 mesh=8 shares=3 oy0=2 oy_n=14 | PASS | 3 tile programs |
| TQ2-053 | program emission structure | h=12 w=23 cin=184 cout=46 k=1 s=1 p=0 mesh=2 shares=2 oy0=0 oy_n=9 | PASS | 2 tile programs |
| TQ2-054 | program emission structure | h=35 w=22 cin=53 cout=47 k=3 s=2 p=1 mesh=8 shares=2 oy0=1 oy_n=13 | PASS | 2 tile programs |
| TQ2-055 | program emission structure | h=22 w=13 cin=153 cout=61 k=1 s=1 p=0 mesh=8 shares=2 oy0=1 oy_n=14 | PASS | 2 tile programs |
| TQ2-056 | program emission structure | h=33 w=28 cin=6 cout=37 k=3 s=2 p=1 mesh=8 shares=4 oy0=5 oy_n=3 | PASS | 4 tile programs |
| TQ2-057 | program emission structure | h=19 w=9 cin=26 cout=17 k=1 s=1 p=0 mesh=8 shares=3 oy0=5 oy_n=7 | PASS | 1 tile programs |
| TQ2-058 | program emission structure | h=15 w=22 cin=177 cout=16 k=3 s=1 p=1 mesh=2 shares=2 oy0=3 oy_n=12 | PASS | 2 tile programs |
| TQ2-059 | program emission structure | h=11 w=34 cin=4 cout=12 k=1 s=2 p=0 mesh=2 shares=4 oy0=1 oy_n=5 | PASS | 1 tile programs |
| TQ3-000 | C driver vs Python backend |  | PASS | C host driver vs Python backend: 4 tile programs compared (descriptors, cfg_m, e |
| TQ5-000 | encoder rejects invalid fetches | cases=5 | PASS | rejected 5/5; len 1 and MAX_FETCH accepted |
| TQ6-000 | bank allocator: bands cover every window |  | PASS | PASS |
| TQ6-001 | bank allocator: output rows inside one band |  | PASS | PASS |
| TQ6-002 | bank allocator: release and reuse |  | PASS | PASS |
| TQ6-003 | bank allocator: exclusion mask on banks |  | PASS | PASS |
| TQ6-004 | bank allocator: exclusion mask on the mesh compile |  | PASS | PASS |
| TQ6-005 | bank allocator: YOLOv8-m 640x640 every group placed |  | PASS | PASS |
| TQ6-006 | bank allocator: 640x640 with tile (3,3) excluded |  | PASS | PASS |
| TQ4-000 | requant model vs RTL arithmetic | samples=20000 | PASS | 0 mismatches |
