#!/usr/bin/env python3
"""Inspect a local MindNLP tokenizer's effective chat-template contract.

This utility intentionally loads only a tokenizer.  It does not load model
weights, initialize an Ascend device, or make network requests.  Use it on a
board to verify the prompt text selected by a profile before starting a long
NPU generation run.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Optional


def _load_tokenizer(path: Path) -> Any:
    from mindnlp.transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(str(path))


def _render(tokenizer: Any, template: Optional[str]) -> str:
    messages = [{"role": "user", "content": "你是谁？"}]
    options: dict[str, Any] = {
        "add_generation_prompt": True,
        "tokenize": False,
    }
    if template:
        options["chat_template"] = template
    rendered = tokenizer.apply_chat_template(messages, **options)
    if not isinstance(rendered, str):
        raise RuntimeError("tokenizer returned a non-text chat template")
    return rendered


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tokenizer-dir", required=True, type=Path)
    parser.add_argument(
        "--template-file",
        type=Path,
        help="UTF-8 text file containing the reviewed profile chat template",
    )
    parser.add_argument(
        "--profile-registry",
        type=Path,
        help="JSON model-profile registry from which to read chat_template",
    )
    parser.add_argument(
        "--profile",
        help="Profile id used with --profile-registry",
    )
    args = parser.parse_args()

    if bool(args.profile_registry) != bool(args.profile):
        parser.error("--profile-registry and --profile must be used together")
    if args.template_file and args.profile_registry:
        parser.error("choose --template-file or --profile-registry, not both")

    tokenizer_dir = args.tokenizer_dir.resolve()
    if not tokenizer_dir.is_dir():
        parser.error("--tokenizer-dir must be an existing directory")
    template = None
    if args.template_file:
        template = args.template_file.read_text(encoding="utf-8")
        if not template.strip():
            parser.error("--template-file must not be empty")
    elif args.profile_registry:
        document = json.loads(args.profile_registry.read_text(encoding="utf-8"))
        profiles = document.get("profiles") if isinstance(document, dict) else None
        if not isinstance(profiles, list):
            parser.error("--profile-registry has no profiles array")
        match = next(
            (item for item in profiles if isinstance(item, dict) and item.get("id") == args.profile),
            None,
        )
        if match is None:
            parser.error("profile was not found in --profile-registry")
        value = match.get("chat_template")
        if not isinstance(value, str) or not value.strip():
            parser.error("selected profile has no non-empty chat_template")
        template = value

    tokenizer = _load_tokenizer(tokenizer_dir)
    rendered = _render(tokenizer, template)
    result: Mapping[str, Any] = {
        "tokenizer_dir": str(tokenizer_dir),
        "tokenizer_type": type(tokenizer).__name__,
        "eos_token": getattr(tokenizer, "eos_token", None),
        "eos_token_id": getattr(tokenizer, "eos_token_id", None),
        "template_source": "profile" if template else "tokenizer-default",
        "rendered_prompt": rendered,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
