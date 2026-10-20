# `04_software` — the host driver and the runtime (drop 0.44, stream D3)

What a host runs against the chip top (`neo_top`): the C driver (`neo_host.c` / `.h`: the register map, the command queue's
images, the telemetry reader, the per-group compiler mirror of the Python backend) and, since drop 0.44, the runtime
(`neo_runtime.c` / `.h`) that loads a compiled network and drives it through the control plane's command queue, with its
Python model (`neo_run.py`) that records the same transactions. The compiler side the runtime consumes is in
`03_models/compiler/`: `neo_onnx.py` (the ONNX importer), `neo_quant.py` (post-training quantization), `neo_sched.py` (the
network's tile programs and the runtime's image), `neo_perf_v3.py --calibrate` (the virtual device). Every number below
is reproduced by the command beside it, from a flat layout (`00_overview/streams/COMMON.md`).

## 1. From an ONNX model to the mesh

| Step | Module | Command |
| --- | --- | --- |
| import an opset-17 model into the compiler's layers, fusions, shapes, unsupported operators named | `neo_onnx.py` | `python3 compiler/neo_onnx.py model.onnx [--lenient]` |
| quantize: INT8 / INT4 weights per output channel, INT8 activations per tensor by calibration, the epilogues, the 2:4 validator | `neo_quant.py` | `python3 compiler/neo_quant.py` (the self-check on the YOLOv8-m group) |
| schedule: tensors placed, groups and steps, the runtime's image | `neo_sched.py` | `python3 compiler/neo_sched.py` |
| run: the image through the runtime (the transaction trace) | `neo_run.py` | `python3 sw/neo_run.py --build yolo --image y.img --trace y.trace --summary` |
| the same in C over a recording device, compared word for word | `neo_runtime.c`, `test_runtime.c` | `make cdriver` (`sim/check_c_runtime.py`) |
| the tests of the importer and the quantizer (31 cases), the tool-qualification kit (147) | | `make onnx`, `python3 compiler/tool_qual.py` |
| the virtual device: the model against the measured cycles | `neo_perf_v3.py` | `make vdev`, `python3 compiler/neo_perf_v3.py --predict` |
| on the RTL: the chip top's T9 (two layers) and T10 (the YOLOv8-m group) | `dv/cocotb/test_neo_top.py` | `make -j1 -C dv/cocotb top` |

### 1.1 The importer's operator coverage (opset 17, the default domain)

| Operator | Imported as | Lowered to the mesh | Checked by |
| --- | --- | --- | --- |
| Conv (group 1, dilation 1, square kernel, symmetric pads) + bias (an input or an Add) + Relu / SiLU (Sigmoid + Mul) | `conv` with `act` | yes: the backend's groups, the bias and SiLU on the vector unit, ReLU in the requantizer | T9, T10, `test_neo_onnx.py` O1 / Q3 |
| Add of two activations | `add`; folded into the conv feeding it as its residual (after the activation, YOLO's bottleneck; or before it) | yes: the residual buffer (FETCH_A arg 1, ADD_ROW) | T10 (four bottleneck residuals), Q3 |
| Concat / Split along channels | `concat` / `split` | yes, as placement: views at channel-tile offsets (a boundary inside a channel tile is refused with the layer named: it would need a host copy) | T10 (the C2f's split and concat) |
| MaxPool (square window, floor mode) | `maxpool` | no: the mesh has no pooling datapath (a strided fetch subsamples, it does not take a maximum); executed by the integer model, a host operator in a deployment | O1, Q2 |
| Resize (nearest, integer scales) | `resize` | no (a host operator; placement would need replicated fetches) | O1, Q2 |
| MatMul / Gemm with a constant weight (+ bias, + GELU as the Erf form or the contrib `Gelu`, + SiLU) | `linear` | through the transformer block path (`block_from_graph` -> `neo_xfmr.block_plan`): M23 / M24's form; a free-standing linear network is not scheduled by `neo_sched` | O1, O3 |
| MatMul of two activations (+ a scalar Div / Mul) | `matmul` with `scale` | the attention tiling of `neo_xfmr` | O1, O3 |
| Softmax (+ an additive mask: the causal mask recognised) | `softmax` | the vector unit's two rounds | O1, O3 |
| LayerNormalization (last axis); the RMS-norm form Pow / ReduceMean / Add / Sqrt / Div / Mul | `layernorm` / `rmsnorm` | the vector unit's statistics and apply rounds | O1, O3 |
| Transpose, Reshape, Flatten, Identity, Unsqueeze, Squeeze (static) | layout | inside the block path (K^T by the drain's transpose) | O1 |
| anything else (Tanh, grouped / depthwise Conv, dilations, non-nearest Resize, ...) | refused: `NeoOnnxError` names the node and the reason; `strict=False` lists them and keeps the rest executable | | O2, TQ7 |

The float executor (`run_float`) agrees with onnxruntime within 1e-5 of the output's range on every family (O1, TQ7: 11 +
12 models per run); the executed shapes agree with `onnx.shape_inference`.

### 1.2 Post-training quantization (`neo_quant.py`)

- Weights per output channel: INT8 (scale max |W[c]| / 127) or INT4 (max |W[c]| / 7, the nibbles the NIC expands on a
  FETCH_W with arg 1) for the layers named `int4=[...]`; a layer named `sparse=[...]` must already be 2:4 along its input
  channels (`check_2_4` on the float tensor and again on the integers; rounding keeps every zero) -- the pruning is the
  training flow's (`prune_2_4` is the stand-in in the tests); the packed sparse entries are `pack_words(sparse=True)`'s
  (Q4) for the NCHK = 4 core.
- Activations per tensor, symmetric INT8 (zero point 0: the feeder's padding and the residual buffer hold zeros), the
  threshold by abs-max, a percentile of |x| (99.99 by default) or the MSE-minimising grid search, from a list of
  calibration inputs; the tensors a layout operator connects share one scale (concat, split, resize, maxpool).
- The epilogue per output tile: the requantizer alone (mult / shift per column, zero point 0, the ReLU flag) when the
  layer has no bias, no residual and at most a ReLU; otherwise the vector unit: `MUL_SCALE` with `SC[c]` = the input scale
  times the column's weight scale and `BI[c]` = the bias, the SiLU / GELU table at a range R (a power of two >= 8 covering
  the calibrated pre-activation), `BMUL K0` + `ADD_ROW` for a residual (shifted by 10 bits into the unit's units), `EMIT`;
  then the requantizer to the tensor's scale. The integer executor (`run_int`) runs every program on `model/vec_ref.VecUnit`
  (the bit-exact model of `rtl/vec_unit.sv`) and the requantizer's model: it is the golden of the RTL runs.
- The accuracies on the YOLOv8-m group at a 12 x 12 crop (random weights at the published widths; `python3
  compiler/neo_quant.py`, `test_neo_onnx.py` Q5, 4 calibration inputs, 2 test inputs, the graph's output tensor):

| Quantization | cos | SQNR | max error (output LSB) |
| --- | --- | --- | --- |
| INT8, percentile 99.99 | 0.99925 | 28.2 dB | 3.6 |
| INT8, MSE | 0.99923 | 28.1 dB | 4.0 |
| INT8, abs-max | 0.99924 | 28.2 dB | 4.9 |
| INT4 weights on the C2f's six convs (the rest INT8) | 0.98710 | 15.8 dB | 18.5 |
| 2:4 on `b2.m0.cv1` (pruned, validated), the rest INT8 | 0.99925 | | |

The transformer blocks keep `neo_xfmr`'s own quantization (one weight scale for INT8, per-output-channel scales for
INT4, the activations calibrated on the float run): through the importer (O3) M23's ONNX form compiles to a plan equal to
`block_plan`'s on the same float32-rounded weights in every phase and every bank word, its output 2.01 LSB from
onnxruntime's; M24's (INT4 weights, causal, SiLU) 11.75 LSB from onnxruntime (2.36 from the float block with the quantized
weights: the INT4 weights carry the rest).

## 2. The runtime (`neo_runtime.c` / `.h`, `neo_run.py`)

The runtime takes a `NetImage` (`neo_sched.py`: the loads, the steps, the read-backs; a text form `NetImage.write`) and a
device (`neo_dev_t`: the control plane's AXI4-Lite write / read / poll, the host bridge's write / read into a bank, the
host's own memory behind the memory bridge) and runs this sequence -- the Python and the C forms issue the same
transactions, compared on eight traces by `sim/check_c_runtime.py` (two networks x the queue and the window mode x
telemetry on / off; 675 .. 21,966 transactions each) and again inside T9 / T10:

1. **init**: ID (`'NEOT'`), VERSION, CONFIG (the geometry must be the image's); CTRL bit 1 on every tile (every cause
   cleared); the memory bridge's window base; IRQ_STATUS cleared; the queue reset (CQ_CTRL bit 1) and configured -- a ring
   of 4 commands at command-memory word 0, a completion ring of 4 at word 16, the images from word 32 (at most 496
   entries a command; the scheduler packs steps to that) --, the command interrupt unmasked, the queue enabled.
2. **loads**: every tensor into its home: the memory window (the host writes its DRAM) or a tile's bank through the host
   bridge, then a one-word read through the same bridge as the fence (`neo_top_integration.md` §5).
3. **a command per step**: the LUT tables through the tiles' windows (256 entries each: too large for an image; cached per
   tile, so a table is written once), the step's image into the command memory (per tile the 20 descriptor registers,
   cfg_m, the requantizer's 8 .. 32 table rows and the ReLU flag, the vector unit's SC / BI / K tables and program through
   VEC_ADDR / VEC_DATA, the DMA program; then TEL_CTRL = 2 per tile -- a fresh telemetry window -- and the CTRL start
   broadcast to the mask), the command (mask, image word and entry count, tag = step + 1) in its ring slot, the doorbell
   (CQ_TAIL = (step + 1) mod 4); the completion polled on CC_TAIL, IRQ_STATUS read (bit 4 must be set) and cleared, the
   record read (tag, entries played and the error bit checked, the global timer kept), CC_HEAD advanced; every tile's
   telemetry snapshot (TEL_CTRL = 1, the 12 counters) and ERR_CAUSE read (a cause fails the step, decoded by name).
   `mode="window"` writes the same registers through the windows, starts each tile and polls the DONE map (the fallback).
4. **read-backs** through the host bridge or from the memory, compared with the integer executor's words.

`neo_host.h` holds the whole register map of `host_if.sv` and the 18 causes + the watchdog by name (review finding 6;
`sim/check_regmap.py` parses the RTL's header and decode and `neo_tile.sv`'s flags and compares -- 22 registers, 19
causes), `neo_ls_pair_set` writes LS_PAIR as the indices first and the enable second (finding 12), and a group of no
output rows is refused by the Python backend and by `neo_compile_group` / `neo_cq_image` (-3; finding 13).

The trace format (`neo_run.TraceDevice`, `test_runtime.c`): `W addr value` (a control-plane write), `R addr tag` (a read;
the tag says what the value is checked for), `P addr mask want` (a poll), `H node word n words...` (a host-bridge
write), `G node word n name` (a host-bridge read-back), `M word n words...` (a memory write), `N word n name` (a memory
read-back), `# ...` (a comment, not compared).

## 3. On the RTL: the chip top's T9 and T10 (`make -j1 -C dv/cocotb top`)

| Test | Network | Commands / tile programs / transactions | Simulated | Result |
| --- | --- | --- | --- | --- |
| T9 | conv 20 -> 8 3x3 (bias, SiLU on the unit) then conv 8 -> 7 1x1 (bias); the input in tile 3's bank through the host bridge, the weights in the memory window, the output in tile 0's bank | 2 / 2 / 675 (the C runtime's 675 identical) | 12.4 us | records (tag 1, 92 entries, timer 5,762 aclk), (2, 80, 7,558); l1 2,584 cycles / 972 array-valid rows; l2 612 / 36; the output bit-exact |
| T10 | the YOLOv8-m group: b0 3 -> 48 3x3 s2, b1 48 -> 96 3x3 s2, b2.c2f (cv1 96 -> 96, two bottlenecks 48 -> 48 3x3 with residuals, concat, cv2 192 -> 96); a 12 x 12 crop of the 640^2 input, INT8 percentile PTQ; every tensor in the memory window | 27 / 102 / 21,966 (the C runtime's identical) | 363 us (99 s of Verilator) | the output bit-exact against the integer executor; PTQ against FP32 on 5 inputs: cos 0.99932, SQNR 28.6 dB, max 5.8 LSB |

The scale: the chip top's cocotb build is 2 x 2 tiles of 16 x 8 cores (ACC_ROWS 64, ABUF 256, WBUF 512), so a group drains
at most 63 rows and a 3 x 3 layer with 48 input channels splits its 54 K-passes over two tiles; the crop keeps every
layer to one row chunk (b0: 36 output pixels, the rest 9). On the 16 x 8 core each output tile of 8 channels is drained
padded to a channel tile of 16 (DR_P), so a layer streams 2 x the K-passes the silicon core would (there cpt = 32 =
ROWS, nothing is padded). The measured cycles per layer (the telemetry window of the step's first-snapshotted tile,
`measured_t10.json`; `make vdev` prints the table with the model beside it):

| Layer | steps | measured cycles | array-valid rows | MAC utilization (window) | model | error |
| --- | --- | --- | --- | --- | --- | --- |
| b0.conv (3 -> 48, M = 36) | 2 | 3,139 | 1,944 | 18.9 / 22.7 % | 3,642 | +16 % |
| b1.conv (48 -> 96 s2, K-split 2, M = 9) | 6 | 18,861 | 5,832 | 7.7 % | 19,176 | +1.7 % |
| b2.cv1 (96 -> 96 1x1) | 3 | 6,249 | 1,296 | 5.2 % | 6,651 | +6.4 % |
| b2.m0.cv1 / m1.cv1 (48 -> 48 3x3, K-split 2) | 3 + 3 | 9,166 + 9,164 | 2,916 x 2 | 8.0 % | 9,078 x 2 | −1.0 % |
| b2.m0.cv2 / m1.cv2 (+ residual) | 3 + 3 | 9,425 + 9,421 | 2,916 x 2 | 7.7 % | 9,414 x 2 | −0.1 % |
| b2.cv2 (192 -> 96 1x1) | 4 | 14,787 | 2,592 | 5.8 % | 14,732 | −0.4 % |
| total | 27 | 80,212 | 25,328 | 8.0 % | 81,186 | +1.2 % |

The utilization at this crop is set by the sequencer's pass spacing (LAT + ROWS + 4 = 60 cycles a K-pass against 9 .. 36
rows a group: the feeder-stall counter holds 1,347 of b1's 3,143 cycles) and by the bridge's serving of four tiles' fetches
one request at a time; at 640^2 a group streams the accumulator's 511 rows a pass on the silicon core (M25's measured
25 .. 28 % on the 36-row layers, the model's 85 % on long groups).

## 4. The virtual device (`neo_perf_v3.py --calibrate compiler/perf_measured_044.json`, `make vdev`)

The perf model takes the measured cycles of `perf_measured_044.json` (built by `neo_perf_v3.py --collect` from a `top`
T10 run and a `tile` run of M21 / M23 / M24 / M25; the bridge's 50.3 bytes per aclk cycle from T3b) and prints the error
table: the conv step model for the runtime's schedule on the chip top (per tile the instructions, the fetches as bridge
requests, the K-passes at the pass spacing, the vector unit's rows, the drained words and partial sums; the step the
slowest tile plus the bridge's serving of the others' fetches and the host's part of the window; three terms fitted on
T10's steps: host 200, fetch round trip 19 cycles, 7.6 words per clk), `tile_layer` with the pass spacing on M25's
single-tile layers, C2's phase model on the transformer plans.

| Measured set | points | measured | model | error |
| --- | --- | --- | --- | --- |
| T10, the 27 steps (17 distinct) | 27 | 80,212 | 81,186 | +1.2 % total; per step −1.0 .. +6.4 % but b0's two steps +14 / +18 % |
| M25, single-tile layers from a bank | 4 | 3,866 / 1,572 / 2,434 / 3,270 | 3,720 / 1,380 / 2,361 / 3,216 | −3.8 / −12.2 / −3.0 / −1.7 % |
| M21, the attention head (16 phases) | 1 | 53,838 | 55,672 | +3.4 % (per phase mean 2.7 %) |
| M23, the ViT block (40 phases) | 1 | 70,561 | 70,708 | +0.2 % (per phase mean 7.4 %, worst −17 %) |
| M24, the LLM layer (80 phases) | 1 | 82,760 | 82,027 | −0.9 % (per phase mean 7.4 %, worst +25 %) |
| all | 34 | | | mean |error| 2.8 %, 31 of 34 within 10 %, worst 18.3 % |

The gaps named: the host's register plays and polls inside a measured window (the fitted `host` term; the per-tile
snapshots are read one after another, 208 cycles apart, so a step's window is its first tile's); the bridge's request
queue (four RDREQs outstanding: the fitted round trip is the average over the queue's states, which is why the
one-channel-tile first layer, whose fetches are short, is predicted 14 .. 18 % high); a K-split owner's wait for its
contributors (modelled as the reduce rows only). `neo_perf_v3.py --predict` gives the virtual device's per-layer
cycles and utilization of the group without an RTL run.
