# Q4F16 ONNX Migration and Decode Rate

## Scope

This record tests whether the public Qwen1.5 q4f16 ONNX graph can be migrated
from ORT `MatMulNBits` operators to a standard ONNX graph accepted by CANN 8.0
ATC. It then measures PyACL decode throughput against the official FP16 graph
from the same Hugging Face revision.

The resulting model is not an INT4 NPU model. It materializes four-bit weights
as FP16 constants. Quantized storage and INT4 compute benefits are therefore not
preserved.

## Source Artifacts

The migration source and unquantized baseline come from:

```text
repository = onnx-community/Qwen1.5-0.5B-Chat-ONNX
revision   = 6d413dd9a252749e0760902c93331e3e4e65b73c
```

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| q4f16 source ONNX | 485,369,358 | `320e83b2f9e996dd77ba933e820c8f6c78b5d60b5a7fa8ed62f8453143eb11cf` |
| migrated FP16 ONNX | 928,515,726 | `9707358bef80ab1d83567581fbbc352eb5e672dc6e21f1277b297016ca99d090` |
| official FP16 ONNX | 928,499,243 | `1397b07c02c5821316ca20cb64f45af87b87932eddd13c743d988d5a7c826262` |
| normalized official FP16 ONNX | 928,499,258 | `22102190a6241d91ec8e89bebc0c9aeb3443c9feb907187d3090e4935145b4b4` |

The migrated graph was regenerated independently on 8T and 20T. Both boards
produced the same derived ONNX SHA-256.

## Graph Migration

The source graph contains 168 `com.microsoft.MatMulNBits` nodes and no other
custom operators. Each q4 weight uses:

```text
bits        = 4
block_size  = 32
zero point  = 8
scale dtype = float16
```

The packing layout was verified against ORT output rather than guessed:

- the low nibble is the even K position;
- the high nibble is the following odd K position;
- scale order is `[N, K_blocks]`;
- logical dequantized shape is `[K, N]`;
- symmetric dequantization is `(q - 8) * scale`.

The migration tool:

1. verifies initializer-backed `MatMulNBits` inputs;
2. unpacks and dequantizes all 168 weights;
3. replaces every node with a standard `MatMul` and FP16 initializer;
4. removes the `com.microsoft` opset domain;
5. makes ONNX Runtime's implicit embedding `Transpose perm` explicit because
   ATC requires the attribute.

The official FP16 baseline also contains the same implicit `Transpose`. It was
normalized separately without changing its weights.

## CPU Numerical Gate

Both graphs were run with ONNX Runtime 1.19.2 CPU at:

```text
input token = 1
past length = 0
KV heads    = 16
head size   = 64
```

| Metric | Result |
| --- | ---: |
| logits cosine | 0.9999999403953552 |
| logits max absolute difference | 0.0004115104675292969 |
| logits mean absolute difference | 0.00008606024493928999 |
| Top-1 original / migrated | 13 / 13 |
| Top-5 intersection | 5/5 |
| maximum present-KV absolute difference | 0.00390625 |
| maximum mean present-KV absolute difference | 0.00006198883056640625 |

The migration is numerically faithful to the original ORT q4f16 graph.

## ATC Results

All conversions used one-token decode shapes:

```text
input_ids            1,1
attention_mask       1,2
position_ids         1,1
each past key/value  1,16,1,64
```

| Target | Model | ATC | OM bytes | OM SHA-256 |
| --- | --- | --- | ---: | --- |
| Ascend310B4 / 8T | migrated FP16 | pass | 1,243,905,055 | `2fa9dda39dd76a19a25f7502b8def11cdb34b60643a8bdf9d02662304c3833f8` |
| Ascend310B4 / 8T | official FP16 | pass | 1,243,811,569 | `95096a0527299f91ec8d17621effef274b8273899b5fb3a8aaad1b1ae1ed42fe` |
| Ascend310B1 / 20T | migrated FP16 | pass | 1,243,905,043 | `eba0c0756395df04175b8a606f06b6b095d48c27fb26a9fe6373b0104527ddb1` |
| Ascend310B1 / 20T | official FP16 | pass | 1,243,811,503 | `8dcf9110c262a3429b4a436c4e48bf7af3a637146e55e5145c8b432eb2917d4d` |

PyACL loaded all four OMs with 51 inputs and 49 outputs. The 48 KV inputs use
float32 shape `[1,16,1,64]`. For input token 1, both models returned vocabulary
Top-1 token 13.

## Short-Context Decode Rate

Protocol:

- fixed one-token shape with one past token;
- two independent rounds per model and board;
- each round uses two warmups and 20 measured iterations;
- `end-to-end` includes host input copies, synchronous ACL execution, logits
  copy, and all present-KV output copies;
- `execute-only` times resident-buffer `acl.mdl.execute` without output D2H.

| Board | Timing mode | q4-derived FP16 | official FP16 | q4-derived delta |
| --- | --- | ---: | ---: | ---: |
| 8T | end-to-end | 5.9512 token/s | 5.9515 token/s | -0.0054% |
| 20T | end-to-end | 10.2303 token/s | 10.2346 token/s | -0.0417% |
| 8T | execute-only | 5.9953 token/s | 5.9951 token/s | +0.0024% |
| 20T | execute-only | 10.3919 token/s | 10.3955 token/s | -0.0347% |

The differences are at measurement-noise scale.

## Long-Context Decode Rate

Additional B4 OMs were compiled with:

```text
attention_mask       1,1024
each past key/value  1,16,1023,64
```

| Model | OM bytes | OM SHA-256 |
| --- | ---: | --- |
| q4-derived FP16 | 1,243,915,086 | `efa9820b594e0b4c93b2eca721f18d3b358708f6b1bf1c2e98adbfa2c72b5d9e` |
| official FP16 | 1,243,821,602 | `45ffc6824fc6f33394be6f7fe662124318eb756f6766c5e3c3f1302b30f4cb7a` |

| Timing mode | q4-derived FP16 | official FP16 | q4-derived delta |
| --- | ---: | ---: | ---: |
| execute-only, 30 runs | 2.5724 token/s | 2.5612 token/s | +0.44% |
| end-to-end, 10 runs | 2.2372 token/s | 2.2572 token/s | -0.89% |

The execute and end-to-end directions differ, confirming that the sub-percent
differences are noise rather than a retained quantization speed benefit.

## Interpretation

The migration succeeds technically:

```text
public q4f16 ONNX
-> dequantized standard FP16 ONNX
-> B4 and B1 ATC
-> PyACL execution
-> matching Top-1 token
```

But the resulting OM is FP16. It performs at the same rate as the official FP16
model because both graphs execute the same standard FP16 MatMul workload. The
q4-derived model does not execute `MatMulNBits` on Ascend and therefore does not
receive INT4 memory-bandwidth or compute savings.

The migration also expands the ONNX file from 463 MB to 886 MB, approximately
1.91 times larger. OM sizes are effectively identical to FP16.

## Limitations

- The measurements are fixed-shape decode microbenchmarks, not a production
  chat service with HTTP, tokenizer, sampling, or client overhead.
- The dynamic growing-KV graph would require separate OMs per past length or a
  static-cache graph rewrite for full multi-token serving.
- Long-context comparison was measured on B4 only. B1 was verified and measured
  at the short decode shape.
- CPU ONNX Runtime is only the numerical reference. NPU performance came from
  PyACL OM execution.
- The original INT4 OM still does not exist on either 310B target.

## Decision

- q4f16-to-FP16 graph migration: `observed-pass`.
- CPU numerical equivalence: `observed-pass`.
- B4 and B1 ATC: `observed-pass`.
- PyACL load and one token execution: `observed-pass`.
- INT4 execution on NPU: `not achieved`.
- Quantization speed benefit after migration: `none observed`.

The correct engineering conclusion is that graph surgery makes the public model
runnable on PyACL, but it removes the quantized execution path. A deployable
INT4 route still requires an ATC-native quantized export, a CANN quantization
toolchain, or a static-cache graph designed for custom quantized operators.
