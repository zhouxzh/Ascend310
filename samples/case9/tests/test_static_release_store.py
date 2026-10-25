from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from ascend_edge_chat.artifacts import static_release_store as store


class _Response:
    def __init__(self, payload: bytes, content_length: int | None = -1) -> None:
        self._payload = payload
        self.headers = (
            {}
            if content_length == -1
            else {"Content-Length": str(len(payload) if content_length is None else content_length)}
        )

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            value, self._payload = self._payload, b""
            return value
        value, self._payload = self._payload[:size], self._payload[size:]
        return value


def _artifact(name: str, destination: str, payload: bytes) -> store.StaticReleaseArtifact:
    return store.StaticReleaseArtifact(
        name=name,
        source_path="locked/%s" % destination,
        destination=destination,
        expected_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def _lock(*artifacts: store.StaticReleaseArtifact) -> store.StaticReleaseLock:
    return store.StaticReleaseLock(
        source_repository=store.SOURCE_REPOSITORY,
        source_revision=store.SOURCE_REVISION,
        base_model_repository=store.BASE_MODEL_REPOSITORY,
        base_model_revision=store.BASE_MODEL_REVISION,
        target=store.TARGET,
        artifacts=tuple(artifacts),
    )


class StaticReleaseLockTests(unittest.TestCase):
    def test_lock_pins_public_source_base_model_target_and_artifact_facts(self) -> None:
        lock = store.load_static_release_lock()
        self.assertEqual(lock.source_repository, "zhouxzh/ascend310b-llm-om-zoo")
        self.assertEqual(lock.source_revision, "0f516070e1c0566e3c652d43c488cd3cfbad89a3")
        self.assertEqual(lock.base_model_repository, "Qwen/Qwen2.5-0.5B-Instruct")
        self.assertEqual(lock.base_model_revision, "7ae557604adf67be50417f59c2c2f167def9a775")
        self.assertEqual(dict(lock.target), {"soc": "Ascend310B4", "tier": "8T", "cann": "8.0.0", "python": "3.9.25"})
        raw = json.loads(store.DEFAULT_STATIC_RELEASE_LOCK.read_text(encoding="utf-8"))
        self.assertEqual(raw["release_status"], "formal-machine")
        self.assertTrue(raw["license_url"].startswith("https://huggingface.co/"))
        by_name = {item.name: item for item in lock.artifacts}
        self.assertEqual(by_name["onnx"].expected_bytes, 1261082122)
        self.assertEqual(by_name["onnx"].sha256, "b4870df5da9c8cbef4163ceb65d4dc13433f2fd8ed5d2083ef3223d07d1a3c0e")
        self.assertEqual(by_name["license"].source_path, "licenses/qwen/LICENSE")
        self.assertEqual(by_name["license"].sha256, "832dd9e00a68dd83b3c3fb9f5588dad7dcf337a0db50f7d9483f310cd292e92e")
        self.assertEqual(by_name["b4-om"].expected_bytes, 1266010586)
        self.assertEqual(by_name["b4-om"].sha256, "f6650e52ff3908288763ef7957832ade606b0e554fa8fde986932f1ca1140eb8")
        self.assertEqual(
            by_name["controller-contract"].source_path,
            "models/qwen2.5-0.5b-instruct-static-kv-1024/common/qwen25-static-kv-1024-fp32-contract.json",
        )
        self.assertEqual(by_name["tokenizer-lock"].expected_bytes, 258)
        self.assertEqual(by_name["tokenizer-lock"].sha256, "02643b3266f05fe36f5067f0142d0a6f2346bc13722702c901b9d8550f779aab")
        self.assertEqual(by_name["b4-om-upstream-lock"].expected_bytes, 1421)
        self.assertEqual(by_name["b4-om-upstream-lock"].sha256, "7d6b35de8261ce3e5f077a6d6d3b6e19df43a2fba2470a2ef30c0d217a8c2770")

    def test_release_root_must_be_absolute_and_not_a_symlink(self) -> None:
        with self.assertRaises(store.StaticReleaseError):
            store.static_release_root("relative-assets")


class StaticReleaseTransferTests(unittest.TestCase):
    def test_download_uses_only_fixed_mirror_and_writes_atomically(self) -> None:
        payload = b"locked-asset"
        artifact = _artifact("fixture", "common/fixture.bin", payload)
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            store, "load_static_release_lock", return_value=_lock(artifact)
        ), mock.patch.object(store, "urlopen", return_value=_Response(payload)) as opened:
            result = store.download_static_release_artifacts(Path(directory), materialize_runtime_locks=False)
            target = Path(directory) / "common" / "fixture.bin"
            self.assertEqual(target.read_bytes(), payload)
            self.assertFalse((target.parent / ".fixture.bin.part").exists())
            self.assertEqual(result["source_kind"], "hf")
            request = opened.call_args.args[0]
            self.assertTrue(request.full_url.startswith("https://hf-mirror.com/zhouxzh/ascend310b-llm-om-zoo/resolve/"))
            self.assertIn(store.SOURCE_REVISION, request.full_url)

    def test_download_allows_chunked_response_when_bytes_and_sha_match(self) -> None:
        payload = b"chunked-locked-asset"
        artifact = _artifact("fixture", "common/fixture.bin", payload)
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            store, "load_static_release_lock", return_value=_lock(artifact)
        ), mock.patch.object(store, "urlopen", return_value=_Response(payload)):
            result = store.download_static_release_artifacts(
                Path(directory), materialize_runtime_locks=False
            )
            self.assertEqual((Path(directory) / "common" / "fixture.bin").read_bytes(), payload)
        self.assertEqual(result["source_kind"], "hf")

    def test_download_rejects_an_alternate_mirror_without_network(self) -> None:
        artifact = _artifact("fixture", "fixture.bin", b"x")
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            store, "load_static_release_lock", return_value=_lock(artifact)
        ), mock.patch.object(store, "urlopen") as opened:
            with self.assertRaisesRegex(store.StaticReleaseError, "hf-mirror"):
                store.download_static_release_artifacts(Path(directory), mirror="https://example.invalid")
        opened.assert_not_called()

    def test_download_rejects_bad_length_and_removes_partial_file(self) -> None:
        artifact = _artifact("fixture", "fixture.bin", b"expected")
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            store, "load_static_release_lock", return_value=_lock(artifact)
        ), mock.patch.object(store, "urlopen", return_value=_Response(b"unexpected", content_length=9)):
            with self.assertRaisesRegex(store.StaticReleaseError, "Content-Length"):
                store.download_static_release_artifacts(Path(directory))
            self.assertFalse((Path(directory) / ".fixture.bin.part").exists())
            self.assertFalse((Path(directory) / "fixture.bin").exists())

    def test_verify_rejects_a_destination_that_escapes_root(self) -> None:
        payload = b"fixture"
        artifact = _artifact("fixture", "../escape.bin", payload)
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            store, "load_static_release_lock", return_value=_lock(artifact)
        ):
            with self.assertRaisesRegex(store.StaticReleaseError, "escapes"):
                store.verify_static_release_artifacts(Path(directory))

    def test_offline_import_is_explicitly_unattested_without_manifest(self) -> None:
        payload = b"offline-fixture"
        artifact = _artifact("fixture", "common/fixture.bin", payload)
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as destination_dir:
            source = Path(source_dir)
            fixture = source / "common" / "fixture.bin"
            fixture.parent.mkdir()
            fixture.write_bytes(payload)
            with mock.patch.object(store, "load_static_release_lock", return_value=_lock(artifact)):
                result = store.import_static_release_artifacts(
                    Path(destination_dir), source, materialize_runtime_locks=False
                )
                self.assertEqual(result["source_kind"], "offline-unattested")
                with self.assertRaisesRegex(store.StaticReleaseError, "requires offline-bundle"):
                    store.import_static_release_artifacts(
                        Path(destination_dir),
                        source,
                        require_manifest=True,
                        materialize_runtime_locks=False,
                    )

    def test_verify_derives_schema_two_runtime_locks_from_public_schema_one_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            om_payload = b"board-om"
            source_om_lock = json.dumps(
                {
                    "schema_version": 1,
                    "bytes": len(om_payload),
                    "sha256": hashlib.sha256(om_payload).hexdigest(),
                },
                sort_keys=True,
            ).encode("utf-8")
            payloads = {
                "onnx": ("common/model.onnx", b"onnx"),
                "tokenizer": ("common/tokenizer.json", b"tokenizer"),
                "tokenizer-config": ("common/tokenizer_config.json", b"{}"),
                "controller-contract": ("contracts/controller.json", b"controller"),
                "b4-om": ("Ascend310B4/model.om", om_payload),
                "b4-descriptor-contract": ("Ascend310B4/descriptor.json", b"descriptor"),
                "tokenizer-lock": ("upstream/common/tokenizer.json.lock.json", b"upstream-tokenizer-lock"),
                "b4-om-upstream-lock": ("upstream/Ascend310B4/model.om.lock.json", source_om_lock),
            }
            artifacts = []
            for name, (destination, payload) in payloads.items():
                path = root / destination
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
                artifacts.append(_artifact(name, destination, payload))
            with mock.patch.object(store, "load_static_release_lock", return_value=_lock(*artifacts)):
                result = store.verify_static_release_artifacts(root)
                second = store.verify_static_release_artifacts(root)
            runtime_lock = json.loads((root / "Ascend310B4" / "qwen25-static-kv-1024-v2.om.lock.json").read_text(encoding="utf-8"))
            tokenizer_lock = json.loads((root / "common" / "tokenizer.json.lock.json").read_text(encoding="utf-8"))
            self.assertEqual(runtime_lock["schema_version"], 2)
            self.assertEqual(runtime_lock["soc_version"], "Ascend310B4")
            self.assertEqual(runtime_lock["sha256"], hashlib.sha256(om_payload).hexdigest())
            self.assertEqual(runtime_lock["controller_contract_sha256"], hashlib.sha256(b"controller").hexdigest())
            self.assertEqual(runtime_lock["runtime_contract_sha256"], hashlib.sha256(b"descriptor").hexdigest())
            self.assertEqual(runtime_lock["source_onnx"]["sha256"], hashlib.sha256(b"onnx").hexdigest())
            self.assertEqual(runtime_lock["source_om_lock"]["schema_version"], 1)
            self.assertEqual(tokenizer_lock["artifact"], "tokenizer.json")
            self.assertEqual(tokenizer_lock["sha256"], hashlib.sha256(b"tokenizer").hexdigest())
            self.assertIn("runtime_locks", result)
            self.assertEqual(result["runtime_locks"], second["runtime_locks"])


if __name__ == "__main__":
    unittest.main()
