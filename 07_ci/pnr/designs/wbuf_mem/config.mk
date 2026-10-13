# wbuf_mem -- the weight buffer: 12 SECDED lanes around the 512 x 936 pair leaf (the NCHK = 4 entry of drop 0.42), here
# fakeram7_512x936_g2_1w1r (121.4 x 166.6 um); 6.5 k cells -- drop 0.43, stream A3
export DESIGN_NAME = wbuf_mem
include $(dir $(DESIGN_CONFIG))../neo_common.mk
