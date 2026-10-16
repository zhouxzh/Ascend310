"""第5章样例 3：数据集与预处理管道。

用固定随机源构造 `GeneratorDataset`，经过 `map`（归一化）与 `batch` 后检查批数、
张量形状，以及两次构建是否得到完全相同的首批数据。

注意：`map` 里的 Python 函数返回 **numpy 数组并显式指定 dtype**；返回未定型的
标量会让数据管道报 "Failed to create tensor from numpy array, data type is unknown"。
"""

from __future__ import annotations

import argparse

from chapter5_utils import FAIL, PASS, emit_result, ensure_output_dir, print_section, write_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a reproducible MindSpore data pipeline.")
    parser.add_argument("--samples", type=int, default=64, help="Number of generated samples.")
    parser.add_argument("--batch-size", type=int, default=8, help="Batch size.")
    parser.add_argument("--seed", type=int, default=0, help="Random seed for the generator.")
    parser.add_argument("--output", default="03_dataset_pipeline.json", help="JSON report file name under outputs/.")
    return parser.parse_args()


def build_dataset(samples: int, batch_size: int, seed: int):
    import numpy as np
    from mindspore.dataset import GeneratorDataset

    def generator():
        rng = np.random.default_rng(seed)
        for _ in range(samples):
            vector = rng.normal(size=(4,)).astype(np.float32)
            label = np.float32(vector.sum())
            yield vector, np.asarray([label], dtype=np.float32)

    def normalize(vector):
        return ((vector - np.float32(0.5)) / np.float32(2.0)).astype(np.float32)

    dataset = GeneratorDataset(generator(), column_names=["x", "y"], num_parallel_workers=1)
    dataset = dataset.map(
        operations=normalize,
        input_columns=["x"],
        output_columns=["x"],
        num_parallel_workers=1,
        python_multiprocessing=False,
    )
    return dataset.batch(batch_size, drop_remainder=True)


def consume(dataset) -> tuple[int, list[float], list[int]]:
    batches = 0
    first_batch: list[float] = []
    shapes: list[int] = []
    for item in dataset.create_dict_iterator():
        if batches == 0:
            first_batch = [round(float(value), 6) for value in item["x"].asnumpy().reshape(-1)[:4]]
            shapes = list(item["x"].shape)
        batches += 1
    return batches, first_batch, shapes


def main() -> int:
    args = parse_args()

    print_section("pipeline")
    batches, first_batch, shapes = consume(build_dataset(args.samples, args.batch_size, args.seed))
    expected_batches = args.samples // args.batch_size
    print(f"batches={batches} (expected {expected_batches}) batch_shape={shapes}")
    print(f"first_batch_head={first_batch}")

    print_section("reproducibility")
    again_batches, again_first, _ = consume(build_dataset(args.samples, args.batch_size, args.seed))
    reproducible = first_batch == again_first and batches == again_batches
    print(f"same seed -> same first batch: {reproducible}")

    ok = batches == expected_batches and shapes == [args.batch_size, 4] and reproducible
    status = PASS if ok else FAIL

    report = {
        "samples": args.samples,
        "batch_size": args.batch_size,
        "seed": args.seed,
        "batches": batches,
        "expected_batches": expected_batches,
        "batch_shape": shapes,
        "first_batch_head": first_batch,
        "reproducible": reproducible,
    }
    report_path = write_json(ensure_output_dir() / args.output, report)

    emit_result(
        "03_dataset_pipeline",
        status,
        {
            "report": str(report_path),
            "batches": batches,
            "batch_shape": shapes,
            "reproducible": reproducible,
        },
    )
    return 0 if status == PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
