# Quantized ONNX Screening

## Scope

This record answers whether public quantized Qwen ONNX files can be downloaded
and sent directly through ATC and PyACL on Ascend 310B without local
quantization.

The answer is no for every candidate screened so far. PyACL loads OM, not ONNX.
An ONNX file must first pass the target CANN version's ATC parser. Artifact
integrity, graph domain, operator support, shape freezing, OM generation, PyACL
execution, numerical consistency, quality, and performance are separate gates.

No local AMCT quantization was performed for these results.

## Candidate Lock

[`online-quantized-qwen-candidates.json`](../configs/online-quantized-qwen-candidates.json)
pins 12 unique public candidates by repository, full revision, bytes, SHA-256,
license status, family, and screening priority. Equivalent files are recorded
as aliases rather than downloaded twice.

The static screening tool is
[`screen-online-quantized-onnx.py`](../scripts/verify/screen-online-quantized-onnx.py).
It hashes the selected graph, checks the ONNX model without loading external
weights, records domains/operators/shapes, and marks the candidate as:

```text
untested
artifact-mismatch
blocked-domain
blocked-operator
atc-eligible
```

`atc-eligible` only means the static graph has no known blocker. It is not an
ATC, OM, PyACL, numerical, or quality pass.

## Candidate Families

| Family | Unique public files | Screening result |
| --- | --- | --- |
| Qwen1.5 0.5B Chat | `model_quantized`, `model_uint8`, `model_q4`, `model_q4f16` | all blocked |
| Qwen2.5 0.5B Instruct GQA | `model_uint8` (also published as `model_quantized`), `model_q4`, `model_q4f16` | all blocked |
| Qwen2.5 0.5B Instruct MHA | `model_uint8` (also published as `model_quantized`), `model_q4`, `model_q4f16` | all blocked |
| Qwen2.5 0.5B ORT int4 | Hazem block-32 graph and external data | blocked before weight download |
| Qwen2.5 0.5B AMD int4 | AMD Ryzen AI graph and external data | blocked before weight download |

The exact bytes and hashes remain authoritative in the candidate lock.

## Previous Int8 Evidence

Two earlier public int8 candidates were downloaded on both boards:

| Candidate | Revision | Graph |
| --- | --- | --- |
| `onnx-community/Qwen2.5-0.5B-Instruct` int8 | `22942cb7d7ba4cc81bb4673549ca4d4614469b5e` | `onnx/model_int8.onnx`, 512,096,557 bytes |
| `Ringoacid/Qwen2.5-0.5B-onnx-attention-int8` | `8b6afbb2b94c52523779b7dba6f5e7ac8d24ef9f` | graph 3,254,466 bytes plus external data 494,222,848 bytes |

Both boards rejected both graphs at ATC conversion with unsupported
`MatMulInteger`, `DynamicQuantizeLinear`, and `DequantizeLinear` operators.
Neither produced an OM.

## Operator Fingerprints

The public files are genuine quantized graphs, but they target ONNX Runtime
execution providers rather than CANN:

- Qwen1.5 `quantized` and `uint8` contain 168 `MatMulInteger`, 96
  `DynamicQuantizeLinear`, and two `DequantizeLinear` nodes.
- Qwen1.5 `q4` and `q4f16` contain 168 `com.microsoft.MatMulNBits` nodes.
- Qwen2.5 GQA `uint8` contains 120 `MatMulInteger`, 96
  `DynamicQuantizeLinear`, 24 `com.microsoft.GroupQueryAttention`, and 48
  `com.microsoft.SkipSimplifiedLayerNormalization` nodes.
- Qwen2.5 GQA `q4` and `q4f16` contain 120 `MatMulNBits`, 24
  `GroupQueryAttention`, and 48 `SkipSimplifiedLayerNormalization` nodes.
- Qwen2.5 MHA `uint8` contains 168 `MatMulInteger`, 96
  `DynamicQuantizeLinear`, 24 `com.microsoft.MultiHeadAttention`, 48
  `com.microsoft.RotaryEmbedding`, and 48
  `SkipSimplifiedLayerNormalization` nodes.
- Qwen2.5 MHA `q4` and `q4f16` replace `MatMulInteger` with 168
  `MatMulNBits` nodes while retaining the vendor attention and normalization
  operators.
- Hazem and AMD int4 graphs contain 121 `MatMulNBits`, 24
  `GroupQueryAttention`, and other `com.microsoft` nodes. Their graph files are
  only about 175-192 KB, but their external weights are about 818-862 MB. The
  static operator gate stopped the test before downloading those weights.

CANN 8.0's ATC parser does not register these operator/domain combinations for
the tested 310B targets.

## ATC Results

The following representative unique operator families were submitted to ATC
with static one-token decode shapes:

| Board | Candidate | ATC result | Stop layer | OM |
| --- | --- | --- | --- | --- |
| Ascend310B4 / 8T | Qwen1.5 uint8 | exit 255 | `MatMulInteger` / dynamic quant parser | no |
| Ascend310B4 / 8T | Qwen1.5 q4 | exit 255 | two domain-version entries; `MatMulNBits` graph | no |
| Ascend310B4 / 8T | Qwen2.5 GQA uint8 | exit 255 | two domain-version entries; custom GQA graph | no |
| Ascend310B1 / 20T | Qwen1.5 uint8 | exit 255 | `MatMulInteger` / dynamic quant parser | no |
| Ascend310B1 / 20T | Qwen1.5 q4f16 | exit 255 | two domain-version entries; `MatMulNBits` graph | no |
| Ascend310B1 / 20T | Qwen2.5 MHA uint8 | exit 255 | two domain-version entries; custom MHA graph | no |

Representative errors:

```text
No parser is registered for Op [...], optype [ai.onnx::14::MatMulInteger]
No parser is registered for Op [...], optype [ai.onnx::14::DynamicQuantizeLinear]
No parser is registered for Op [...], optype [ai.onnx::14::DequantizeLinear]
E16005: The model has [2] [--domain_version] fields, but only one is allowed.
```

These are conversion/parser failures, not memory failures. ATC rejects the graph
before OM allocation or NPU execution. The board service's NPU memory does not
participate in this decision.

The remaining unique files were stopped at the static operator/domain gate
because they reuse an already reproduced failing family. They were not assigned
redundant ATC runs.

## Efficient Distributed Screening

The initial matrix repeated representative ATC tests on both boards. After the
results were stable, the procedure was narrowed:

1. Assign different new candidates to 8T and 20T.
2. The first board performs static graph screening and ATC.
3. If ATC produces no OM, do not repeat that candidate on the second board.
4. If ATC produces an OM, then convert and validate the same source ONNX
   independently on the second board.
5. Only OM-producing candidates proceed to PyACL descriptor, smoke,
   continuous generation, numerical, and performance gates.

This reduces duplicate parser failures without merging the two boards' evidence
sets.

## PyACL and Generation

No candidate in this campaign produced an OM. Therefore:

```text
PyACL descriptor: not reached
PyACL load/execute: not reached
single-token generation: not reached
16-token continuous generation: not reached
accuracy: not reached
performance: not reached
```

A one-token ONNX smoke would not have been sufficient for chat eligibility even
if an OM had been produced. The graph would still need correct KV feedback,
tokenizer behavior, stop handling, and continuous generation.

## Supported Direction

A language model is a PyACL candidate only when:

1. The graph has a fixed contract or can be frozen to a target shape by ATC.
2. Every operator and domain is accepted by the target CANN parser.
3. KV-cache layout and update semantics are explicit.
4. ATC emits a nonempty OM for the correct SoC.
5. The OM descriptor matches the runtime contract.
6. PyACL loads, executes, and feeds cache outputs back correctly.
7. A fixed golden supports numerical comparison.
8. Quality and performance are measured separately.

The current public ORT quantization formats do not satisfy the first two gates.
A future viable route is an ATC-native quantized export or a standard static
graph that uses only registered operators. Installing custom OPPs or changing
CANN is outside this campaign.

## Decision

- All 12 newly locked public candidates are blocked at static graph inspection.
- Six representative unique candidates were submitted to ATC; all six exited
  255 and produced no OM.
- The earlier two int8 candidates also failed ATC on both boards.
- Memory exhaustion is not the cause of these failures.
- No original quantized public Qwen ONNX has reached PyACL in this campaign.
- Public ONNX availability is not evidence of Ascend compatibility.
- The formal FP32 static-KV release remains unchanged.

A later graph-surgery experiment successfully dequantized Qwen1.5 `q4f16` into
standard FP16, converted it for B4 and B1, and executed it through PyACL. That
derived model has matching FP16 numerical behavior but no INT4 execution or
measurable speed advantage. See
[Q4F16 ONNX Migration and Decode Rate](12-q4f16-onnx-migration-and-decode-rate.md).
