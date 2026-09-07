#!/usr/bin/env python3
"""Report MindNLP chat-loader capability without loading model weights.

The command is intended for an activated Ascend board environment.  It is a
read-only compatibility probe: no network access, package changes, model
instantiation, or device allocation are performed.
"""

from __future__ import annotations

import importlib
import json
import sys
from typing import Any


_MODEL_TOKENS = ("qwen", "llama", "minicpm", "deepseek")


def _mapping_keys(module: Any, name: str) -> list[str]:
    mapping = getattr(module, name, None)
    if mapping is None:
        return []
    try:
        keys = list(mapping.keys())
    except Exception as exc:  # pragma: no cover - depends on board package
        return ["ERROR:" + repr(exc)]
    return [
        str(key)
        for key in keys
        if any(token in str(key).lower() for token in _MODEL_TOKENS)
    ]


def probe() -> dict[str, Any]:
    result: dict[str, Any] = {
        "python": sys.executable,
        "python_version": sys.version,
    }
    try:
        mindspore = importlib.import_module("mindspore")
        result["mindspore"] = getattr(mindspore, "__version__", None)
    except Exception as exc:  # pragma: no cover - board diagnostic
        result["mindspore_error"] = repr(exc)
    try:
        mindnlp = importlib.import_module("mindnlp")
        result["mindnlp"] = getattr(mindnlp, "__version__", None)
    except Exception as exc:  # pragma: no cover - board diagnostic
        result["mindnlp_error"] = repr(exc)
    try:
        transformers = importlib.import_module("mindnlp.transformers")
        result["exported_names"] = sorted(
            name
            for name in dir(transformers)
            if any(token in name.lower() for token in (*_MODEL_TOKENS, "causal"))
        )
        result["mappings"] = {
            name: _mapping_keys(transformers, name)
            for name in (
                "CONFIG_MAPPING",
                "MODEL_MAPPING",
                "MODEL_FOR_CAUSAL_LM_MAPPING",
                "TOKENIZER_MAPPING",
            )
        }
    except Exception as exc:  # pragma: no cover - board diagnostic
        result["transformers_error"] = repr(exc)
    return result


def main() -> int:
    print(json.dumps(probe(), ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
