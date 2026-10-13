# acc_bank -- the accumulator: 33 adders, the two-cycle read-modify-write with its forward, the ABFT check around the
# 512 x 1,056 leaf, here the FakeRAM2.0 1-write-1-read macro fakeram7_512x1056_g1_1w1r (137.0 x 166.6 um); 18 k cells,
# 3,207 I/O pins (the row in, the partial-sum and drain rows out) -- drop 0.43, stream A3
export DESIGN_NAME = acc_bank
include $(dir $(DESIGN_CONFIG))../neo_common.mk

export SKIP_LAST_GASP = 1
