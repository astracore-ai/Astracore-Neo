# Stream D3 -- software to an evaluable level: the ONNX importer, post-training quantization, the runtime over the chip
# top's command queue, the virtual device, reference networks measured on the simulated mesh

Branch `d3-software`. Read COMMON.md first, then `00_overview/ROADMAP.md` §1 (D3's row) and the spec §11 (the software
contract), `03_models/compiler/` (`neo_compile.py`: the lowering and `direct_conv` ground truth; `neo_backend.py`: programs,
layouts, `compile_network` with the tile-exclusion map, `pack_words`, the 2:4 compressor; `neo_xfmr.py`: the transformer
operators and block plans; `neo_perf_v3.py` with B2's telemetry inputs; `neo_alloc.py`), `04_software/neo_host.c` /
`neo_host.h` (the register map, the command queue, the telemetry reader), `08_ip_product/neo_top_integration.md`, the
tests that drive networks today (`02_dv/sim/run_two_layer.py`, cocotb `test_two_layer.py`, M18 / M19 / M21 / M23 / M24,
the `top` suite's T3 / T8), `03_models/compiler/backend_yolov8m_640.txt` (the static compile of YOLOv8-m).

## The items

1. **The ONNX importer** (`03_models/compiler/neo_onnx.py`): ONNX (opset 17) to the compiler's layer list -- Conv (+ Add
   / Relu / SiLU / Sigmoid-mul fused), MaxPool (as the DMA's strided fetch or a vector program), Concat / Split / Resize
   (bank layout operations, transpose by DMA), MatMul / Gemm, Softmax, LayerNormalization, Gelu, Add (residual), Transpose,
   Reshape; shapes inferred, unsupported operators reported with the node name. Test against the ONNX runtime's
   float output on a small ONNX model per operator family (the `onnx` and `onnxruntime` pip packages in the python-regress
   job; `tool_qual.py` gains the importer).
2. **Post-training quantization** (`neo_quant.py`): per-channel INT8 weights, per-tensor INT8 activations from a
   calibration set (percentile / MSE), the requant tables (mult / shift / zero point) per layer, the 2:4 pruner's validator
   path for layers the user marks sparse (pruning itself is training-side: the importer accepts an already-2:4 tensor and
   checks it), INT4 weight-only with per-output-channel scales for the layers marked so; the accuracy of each against FP32
   reported on the calibration set.
3. **The runtime** (`04_software/neo_runtime.c` + `.h` over `neo_host.c`): a layer graph's programs and tables loaded into
   banks through the host bridge, descriptors issued through B2's command queue with doorbells, completion records and the
   interrupt per completion polled or waited, results read back through the bridge, the telemetry snapshot per layer; a
   `neo_run` CLI model in Python (`04_software/neo_run.py`) that drives the same sequence against the cocotb `top` suite
   (a T9 test: a two-layer network end to end through the runtime's exact register sequence, the C runtime producing the
   same transactions as the Python -- the C-vs-Python check the backend already has, extended to the runtime).
4. **The virtual device** (`neo_perf_v3` calibrated): the perf model takes the RTL's measured per-layer cycles from the
   telemetry (M25 / T3 / M21–M24) and the bridge's measured bandwidth (B2's T3b), predicts a network's layer times and
   utilization, and reports the error against the measured layers (a table; the model within ~10 % on the measured set
   or the model's gaps named).
5. **Reference networks measured**: a YOLOv8-m layer group (the first conv stack and one C2f block at 640², INT8, through the
   importer and PTQ) on the simulated 2 x 2 mesh under cocotb (`tile32` or `top` scale -- the runner's limits decide the
   crop: say what ran and at what scale), with accuracy against FP32 on the crop and the cycles against the perf model; a
   ViT block (M23 exists) and an LLM layer with INT4 (M24 exists) re-run through the importer path; the results in
   `04_software/README.md` and a `08_ip_product/evaluation.md` an evaluator can follow.

## Verification and report

Local: the python-regress targets (`make regress_ci`, `tool_qual.py`), the new unit tests of the importer / quantizer
(pytest or the repository's script style), the cocotb `top` T9 and the network crop under COMMON.md's memory rules. The
PR, green on every job (the python-regress job gains the ONNX packages: keep its time under 30 min). Report: the operator
coverage table, the PTQ accuracies, the runtime's transaction equality, the virtual device's error table, the measured
network results with cycles and utilization, anything unresolved, the PR number.
