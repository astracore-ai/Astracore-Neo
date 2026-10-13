# prog_mem -- the DMA program memory: 32 x 2 x 39 bits of flops, the two SECDED decoders and (drop 0.43) the registered
# decoded instruction; 13.9 k cells, no macro -- drop 0.43, stream A3
export DESIGN_NAME = prog_mem
include $(dir $(DESIGN_CONFIG))../neo_common.mk
