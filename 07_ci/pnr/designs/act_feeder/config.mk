# act_feeder -- the activation feeder: the duplicated incremental address generators around the 1,024 x 512 pair leaf,
# here fakeram7_1024x512_g2_1w1r: 66.5 x 331.8 um, a 5:1 macro (the pair of 32-byte entries is only 512 bits wide).
# 8.7 k cells; at 55 % a square core would be ~205 um on a side, lower than the macro, so the core is three times as
# high as wide (~118 x 354 um) -- drop 0.43, stream A3
export DESIGN_NAME = act_feeder
include $(dir $(DESIGN_CONFIG))../neo_common.mk

export CORE_ASPECT_RATIO = 3
