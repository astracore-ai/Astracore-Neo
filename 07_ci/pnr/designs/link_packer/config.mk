# link_packer -- 16-word beats from the NIC into 32-word link flits (drop 0.42's merging packer with its input register;
# 24 k cells, no macro). 569 inputs and 1,079 outputs (the link flit): at 55 % the die's edges hold them at the default
# pin spacing (drop 0.43, stream A3)
export DESIGN_NAME = link_packer
include $(dir $(DESIGN_CONFIG))../neo_common.mk
