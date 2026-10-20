# Evaluating the AstraCore Neo software stack (drop 0.44, stream D3)

What an evaluator runs, in order, from a checkout of the repository: the ONNX importer, the post-training quantization,
the runtime over the chip top's command queue, the virtual device, and the reference networks on the simulated mesh. Every
step names its command and the output it must give; the numbers are those of the stream's own runs (20 Oct 2026,
Verilator 5.053 / Python 3.12 / onnx 1.23 / onnxruntime 1.31; the CI run of the pull request repeats them on
Verilator 5.020). `04_software/README.md` has the mechanisms behind each number.

## 0. Setting up (10 minutes)

```
pip install numpy onnx onnxruntime cocotb==1.9.2      # python-regress's packages; cocotb and Verilator >= 5.0 for step 4
W=<checkout>; F=<a directory outside the checkout>      # the flat layout (00_overview/streams/COMMON.md): copies or links
mkdir -p $F && cd $F && cp -r $W/01_rtl rtl && cp -r $W/02_dv/tb tb && cp -r $W/02_dv/dv dv && cp -r $W/02_dv/sim sim
cp -r $W/03_models/model model && cp -r $W/03_models/compiler compiler && cp -r $W/04_software sw && cp $W/07_ci/Makefile Makefile
```

## 1. The ONNX importer (1 minute)

```
python3 compiler/test_neo_onnx.py
```
Expected: `31/31 cases passed`, `RESULT: ALL PASS`. The O1 lines list one model per operator family (convolution with
its fusions, the layout operators, the linear layers with GELU / SiLU, attention with the causal mask, the two norms)
imported and executed within `rel err` 1e-7 .. 1e-6 of onnxruntime; O2 shows the refusal of an unsupported operator by
node name (`node 'tanh_bad' (Tanh): operator not supported by the importer`) and the lenient report listing the grouped
and the dilated convolution with their reasons; O3 shows the two transformer blocks of drop 0.43 (M23, M24) recognised
from their ONNX form and compiled to plans identical to the direct ones. The operator table is in
`04_software/README.md` §1.1. To import a model of your own: `python3 compiler/neo_onnx.py model.onnx` prints the layer
table or the first unsupported node (`--lenient` lists them all).

## 2. Post-training quantization (1 minute)

```
python3 compiler/neo_quant.py
```
Expected, on the YOLOv8-m group (the first conv stack and the first C2f block at the published widths, random weights,
a 12 x 12 crop; 4 calibration inputs, 2 test inputs): `INT8 percentile: output cos 0.99925, SQNR 28.23 dB, max |err| 3.59
LSB`, MSE `0.99923 / 28.11 dB`, abs-max `0.99924 / 28.21 dB`, `INT4 weights (the C2f convs): output cos 0.98710, SQNR 15.83
dB`, the 2:4 validator refusing the dense layer (`weights are not 2:4 along the input channels: output channel 0,
channels 0..3, ...`) and accepting the pruned one, `RESULT: ALL PASS`. Per-layer cos / SQNR / max LSB tables come from
`neo_quant.accuracy_report`; the integer executor behind them runs the vector unit's programs on the bit-exact model of
`rtl/vec_unit.sv` and the requantizer's model -- what the RTL drains (step 4 proves it).

## 3. The runtime: the C driver and runtime against the Python model (1 minute)

```
make cdriver
```
Expected: `C host driver vs Python backend: 9 tile programs and command-queue images compared ... -> ALL PASS` (the
backend's groups, now with the runtime's drain format, INT4 entries and residual fetch, and the refusal of a group of
no rows), `register map and causes: 22 registers, 19 causes checked -> ALL PASS` (`sim/check_regmap.py`: `neo_host.h`
against `host_if.sv` and `neo_tile.sv`), and `C runtime vs Python runtime: 8 traces compared -> ALL PASS` with the eight
lines `two_layer queue telemetry=True : 675 transactions identical` .. `yolo window telemetry=False: 10819 transactions
identical`. The sequence the runtime issues is in `04_software/README.md` §2; `python3 sw/neo_run.py --build yolo --trace
y.trace --summary` writes the trace to read.

`python3 compiler/tool_qual.py` runs the tool-qualification kit: `tool_qual: 147/147 cases passed` (TQ7 the importer
against onnxruntime, TQ8 the quantizer's accuracy bars and the runtime's trace equality, with the kit's earlier cases).

## 4. On the simulated chip top: T12 and T13 (3 minutes; Verilator and cocotb)

```
make -j1 -C dv/cocotb top                      # the whole suite, T1 .. T13; or TESTCASE=t12_runtime_two_layer,t13_runtime_yolov8m_crop
```
Expected: `TESTS=11 PASS=11`. The log's `T12:` line: two commands from one runtime sequence, `675 transactions replayed`,
`the C runtime's 675 transactions identical`, completion records `(1, 92, ...), (2, 80, ...)` with their timers, `output
read back through the host bridge bit-exact against the integer executor`, the telemetry per step (`l1.conv#0 2584
cycles / 972 array-valid rows`). The `T13:` line: `27 commands, 102 tile programs, 21966 transactions in 363418 ns`,
`output bit-exact against the integer executor`, `PTQ INT8 against FP32 on 5 inputs: output cos 0.99932, SQNR 28.59 dB,
max |err| 5.80 LSB`, the measured cycles per layer, and `measured_t13.json` written beside the test (the input of step 5).
The build is the suite's 2 x 2 mesh of 16 x 8 cores with 4,096-word banks and the silicon's 1024-bit links; the crop and
the padded channel tiles it implies are explained in `04_software/README.md` §3. The two tests take 100 s of Verilator on
a laptop (3.6 us of simulation per second).

## 5. The virtual device (10 seconds)

```
make vdev                                      # = python3 compiler/neo_perf_v3.py --calibrate compiler/perf_measured_044.json
python3 compiler/neo_perf_v3.py --predict      # the group's predicted layer times and utilization, no RTL
```
Expected: the error table -- T13's steps (`total (27 steps) 80212 81186 +1.2 %`), M25's single-tile layers (−1.7 ..
−12.2 %), `M21 ... +3.4 %`, `M23 ... +0.2 %`, `M24 ... −0.9 %`, `error table: 34 measured points, mean |error| 2.8 %, 31/34
within 10 %, worst 18.3 %`, and the gaps the model does not carry. To rebuild the measured file from your own runs:
`python3 compiler/neo_perf_v3.py --collect dv/cocotb/measured_t13.json <log of make -C dv/cocotb tile TESTCASE=m21_attention_head,m23_vit_block,m24_llm_layer,m25_telemetry> compiler/perf_measured_044.json`.

## 6. The reference networks

| Network | Path | Scale | Result |
| --- | --- | --- | --- |
| YOLOv8-m: b0, b1, b2.c2f (3 -> 48 -> 96, cv1, 2 bottlenecks with residuals, concat, cv2 192 -> 96) | ONNX (generated, random weights) -> importer -> PTQ INT8 -> scheduler -> runtime (queue) -> the chip top under cocotb (T13) | a 12 x 12 crop of 640^2 on 2 x 2 tiles of 16 x 8 cores; 8 layers, 66 groups, 27 commands | bit-exact against the integer executor; PTQ cos 0.99932 / 28.6 dB; 80,212 cycles measured (model +1.2 %), MAC utilization 8.0 % at this crop (the 60-cycle pass spacing against 9 .. 36-row groups; see README §3) |
| the ViT block (M23: layer norm, attention, GELU MLP, INT8) | ONNX (generated) -> importer -> `block_from_graph` -> `neo_xfmr.block_plan` -> the mesh model (O3); the RTL run is M23 of the tile suite on the identical plan | T = 32, D = d = 32, H = 32 on the 2 x 2 mesh | the plan from the importer identical to the direct one (40 phases, every bank word); output 2.01 LSB from onnxruntime; M23 on the RTL 70,561 cycles, model +0.2 % |
| the LLM layer (M24: RMS norm, causal attention, SiLU MLP, INT4 weights) | the same | T = 32, D = d = 32, H = 64 | identical plan (80 phases); 11.75 LSB from onnxruntime (2.36 from the float block with the INT4 weights); M24 on the RTL 82,760 cycles, model −0.9 % |

What did not run at a larger scale and why: the 640^2 layers (M = 102,400 rows a group) need the silicon configuration's
accumulator (512 rows a drain) and banks; the `tile32` / `mesh8` cocotb builds that have them are CI-only (2 .. 16 GB) and
the chip top's suite is the 16 x 8 build, so the group runs as a crop with the real channel widths and every operator of
the block, and the perf model carries the shape to the silicon scale (`neo_perf_v3.py --ksplit` for the whole network, the
static compile in `backend_yolov8m_640.txt`). MaxPool and Resize (the SPPF and the head's upsampling) are imported and
executed by the integer model, not lowered: the mesh has no pooling datapath (a host operator in a deployment).

## 7. What is not resolved

- A split or concat boundary inside a channel tile (48 channels on the 32-row silicon core) needs a host copy the scheduler
  does not emit yet (it refuses with the layer named); the 16 x 8 build's 8-channel tiles never meet it.
- Linear layers outside the transformer block form (a free-standing MLP) are imported and quantized but not scheduled
  by `neo_sched` (the block path of `neo_xfmr` is the one lowering of MatMul).
- The perf model's first-layer error (+14 .. +18 % on b0's two steps): the pass spacing's overlap with short fetches is
  not modelled; the fit keeps the other 25 steps within 6.4 %.
- The measured utilization of the crop (8 %) is the crop's, not the network's: the runtime's commands and the bridge's
  serving dominate 9-row groups; the silicon figures stay the perf model's.
