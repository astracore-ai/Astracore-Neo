# c16_systolic_array -- the 16x8 core's 16 x 9 PE array (128 data PEs, 16 check-column PEs; 168 k cells, no macro):
# the first real routed block
export DESIGN_NAME = c16_systolic_array
include $(dir $(DESIGN_CONFIG))../neo_common.mk

# runtime: no last-gasp pass in the timing repairs of CTS and global route (the floorplan's repair skips it anyway)
export SKIP_LAST_GASP = 1
