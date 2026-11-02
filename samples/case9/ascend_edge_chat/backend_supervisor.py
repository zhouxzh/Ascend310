"""Loopback-only supervisor for controlled 20T backend transitions.

The browser may name a backend and an allowlisted model ID.  It never supplies
an executable, port, artifact path, token, or environment value.  This process
owns the child gateway and exactly one inference worker, so a transition has a
single cancellation/rollback boundary.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
from pathlib import Path
import signal
import sys
import time
from typing import Any, Mapping, Optional

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .backends.model_profiles import ProfileError, load_profiles
from .model_catalog import DEFAULT_CATALOG_PATH, CatalogError, catalog_by_id, load_catalog
from .static_kv.candidates import CandidateError, DEFAULT_CANDIDATE_CATALOG, load_acl_candidate
from .static_kv.runtime import detect_soc_version


LOGGER = logging.getLogger("ascend_edge_chat.backend_supervisor")
_LOOPBACK_CLIENTS = {"127.0.0.1", "::1", "localhost", "testclient"}
_GATEWAY_URL = "http://127.0.0.1:7861/health"
_MINDSPORE_URL = "http://127.0.0.1:8090/health"
_ACL_URL = "http://127.0.0.1:8084/health"


class SupervisorError(RuntimeError):
    """A controlled backend transition could not complete."""


class SelectBackendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    backend: str = Field(min_length=1, max_length=64)
    model: Optional[str] = Field(default=None, min_length=1, max_length=160)


class BackendSupervisor:
    def __init__(self, *, catalog_path: Path, acl_catalog_path: Path, candidate_data_dir: Path) -> None:
        self.catalog_path = catalog_path
        self.acl_catalog_path = acl_catalog_path
        self.candidate_data_dir = candidate_data_dir
        self._lock: Optional[asyncio.Lock] = None
        self._gateway: Optional[asyncio.subprocess.Process] = None
        self._worker: Optional[asyncio.subprocess.Process] = None
        self._backend = ""
        self._model = ""
        self._started_at = 0.0
        self._last_error = ""

    def _transition_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    @staticmethod
    def _project_root() -> Path:
        return Path(__file__).resolve().parents[1]

    def _mindspore_catalog(self) -> dict[str, dict[str, Any]]:
        try:
            return catalog_by_id(self.catalog_path)
        except CatalogError as exc:
            raise SupervisorError("MindSpore 模型目录无效") from exc

    def _acl_catalog_entry(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.acl_catalog_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SupervisorError("ACL 候选目录无效") from exc
        if not isinstance(raw, Mapping) or raw.get("schema_version") != 1:
            raise SupervisorError("ACL 候选目录无效")
        for backend in raw.get("backends", []):
            if isinstance(backend, Mapping) and backend.get("id") == "acl-static-kv":
                return dict(backend)
        raise SupervisorError("ACL 候选目录没有 static-KV 后端")

    def public_backends(self) -> list[dict[str, Any]]:
        mindspore = self._mindspore_catalog()
        acl = self._acl_catalog_entry()
        return [
            {
                "id": "mindspore",
                "display_name": "MindSpore / MindNLP",
                "active": self._backend == "mindspore",
                "models": [
                    {
                        "id": item["id"],
                        "display_name": item["display_name"],
                        "selectable": item.get("selectable") is True,
                        "technical_status": item["technical_status"],
                        "default_max_tokens": item["default_max_tokens"],
                        "max_tokens": item["max_tokens"],
                    }
                    for item in mindspore.values()
                ],
            },
            {
                "id": "acl-static-kv",
                "display_name": str(acl.get("display_name", "PyACL 固定KV")),
                "active": self._backend == "acl-static-kv",
                "models": [
                    {
                        "id": item.get("id"),
                        "display_name": item.get("display_name"),
                        "selectable": item.get("selectable") is True,
                        "technical_status": item.get("technical_status"),
                        "default_max_tokens": item.get("default_max_tokens"),
                        "max_tokens": item.get("max_tokens"),
                    }
                    for item in acl.get("models", [])
                    if isinstance(item, Mapping)
                ],
            },
        ]

    @staticmethod
    async def _health(url: str) -> Mapping[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=3.0, follow_redirects=False, trust_env=False) as client:
                response = await client.get(url)
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SupervisorError("后端健康检查失败") from exc
        if not isinstance(payload, Mapping):
            raise SupervisorError("后端健康响应无效")
        return payload

    async def _stop_process(self, process: Optional[asyncio.subprocess.Process]) -> None:
        if process is None or process.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(process.wait(), timeout=20.0)
            return
        except asyncio.TimeoutError:
            pass
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(process.wait(), timeout=5.0)

    async def _stop_children(self) -> None:
        await self._stop_process(self._gateway)
        await self._stop_process(self._worker)
        self._gateway = None
        self._worker = None

    async def _spawn(self, command: list[str], *, environment: Mapping[str, str], name: str) -> asyncio.subprocess.Process:
        log_root = self.candidate_data_dir / "logs"
        log_root.mkdir(parents=True, exist_ok=True)
        log_path = log_root / (name + "-%d.log" % int(time.time()))
        log_stream = log_path.open("ab")
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(self._project_root()),
                env=dict(environment),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=log_stream,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
        finally:
            log_stream.close()
        return process

    async def _wait_worker(self, backend: str, model: str, process: asyncio.subprocess.Process) -> Mapping[str, Any]:
        url = _ACL_URL if backend == "acl-static-kv" else _MINDSPORE_URL
        deadline = asyncio.get_running_loop().time() + 180.0
        last_error = ""
        while asyncio.get_running_loop().time() < deadline:
            if process.returncode is not None:
                raise SupervisorError("目标后端提前退出，退出码为 %s" % process.returncode)
            try:
                health = await self._health(url)
                if health.get("ready") is True and health.get("healthy") is True:
                    if backend == "mindspore" and health.get("profile") != model:
                        last_error = "MindSpore profile 尚未切换"
                    elif backend == "acl-static-kv" and health.get("model") != "qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om":
                        last_error = "ACL 模型合同不匹配"
                    else:
                        return health
                else:
                    last_error = str(health.get("last_error") or "后端未就绪")
            except SupervisorError as exc:
                last_error = str(exc)
            await asyncio.sleep(1.0)
        raise SupervisorError("目标后端启动超时：%s" % last_error)

    async def _start_gateway(self, backend: str) -> asyncio.subprocess.Process:
        environment = os.environ.copy()
        environment["PYTHONNOUSERSITE"] = "1"
        environment["ASCEND_EDGE_CHAT_BACKEND"] = backend
        if backend == "mindspore":
            environment["ASCEND_EDGE_CHAT_UPSTREAM_BASE_URL"] = "http://127.0.0.1:8090/v1"
            environment["ASCEND_EDGE_CHAT_UPSTREAM_MODEL"] = "ascend-edge-chat-worker"
        else:
            environment["ASCEND_EDGE_CHAT_UPSTREAM_BASE_URL"] = "http://127.0.0.1:8084/v1"
            environment["ASCEND_EDGE_CHAT_UPSTREAM_MODEL"] = "qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om"
        process = await self._spawn(
            [sys.executable, "-m", "ascend_edge_chat", "gateway", "--host", "127.0.0.1", "--port", "7861"],
            environment=environment,
            name="gateway-" + backend,
        )
        deadline = asyncio.get_running_loop().time() + 30.0
        last_error = ""
        while asyncio.get_running_loop().time() < deadline:
            if process.returncode is not None:
                raise SupervisorError("gateway 提前退出，退出码为 %s" % process.returncode)
            try:
                health = await self._health(_GATEWAY_URL)
                if health.get("status") == "ok":
                    return process
                last_error = str(health.get("detail") or "gateway 上游未就绪")
            except SupervisorError as exc:
                last_error = str(exc)
            await asyncio.sleep(0.5)
        raise SupervisorError("gateway 未通过上游健康门：%s" % last_error)

    async def select(self, backend: str, model: Optional[str]) -> Mapping[str, Any]:
        async with self._transition_lock():
            if backend not in {"mindspore", "acl-static-kv"}:
                raise SupervisorError("backend 必须是 mindspore 或 acl-static-kv")
            selected_model = (model or "").strip()
            if backend == "mindspore":
                catalog = self._mindspore_catalog()
                if not selected_model:
                    selected_model = "qwen1.5-0.5b-chat"
                entry = catalog.get(selected_model)
                if not isinstance(entry, Mapping) or entry.get("selectable") is not True:
                    raise SupervisorError("MindSpore 模型未通过启动门")
                registry = entry.get("worker_registry")
                if not isinstance(registry, str):
                    raise SupervisorError("MindSpore 模型注册表无效")
                try:
                    load_profiles((self._project_root() / registry).resolve()).get(selected_model)
                except (OSError, ProfileError) as exc:
                    raise SupervisorError("MindSpore 模型注册表无效") from exc
                worker_command = [
                    "bash", str(self._project_root() / "scripts" / "run" / "start-worker.sh"),
                    "--host", "127.0.0.1", "--port", "8090",
                    "--profile", selected_model,
                    "--registry", str((self._project_root() / registry).resolve()),
                ]
                worker_environment = os.environ.copy()
                worker_environment["PYTHONNOUSERSITE"] = "1"
            else:
                if detect_soc_version() != "Ascend310B1":
                    raise SupervisorError("ACL B1 候选只能在 Ascend310B1 上启动")
                acl = self._acl_catalog_entry()
                if not selected_model:
                    selected_model = str(acl.get("default_model", ""))
                candidate = load_acl_candidate(
                    selected_model,
                    data_dir=self.candidate_data_dir,
                    catalog_path=self.acl_catalog_path,
                )
                worker_command = [
                    sys.executable, "-m", "ascend_edge_chat", "acl-candidate-worker",
                    "--candidate", candidate.id,
                    "--data-dir", str(candidate.data_dir),
                    "--catalog", str(self.acl_catalog_path),
                    "--host", "127.0.0.1", "--port", "8084",
                    "--max-tokens", str(candidate.max_tokens),
                    "--execution-timeout", "240",
                ]
                worker_environment = os.environ.copy()
                worker_environment["PYTHONNOUSERSITE"] = "1"
            await self._stop_children()
            self._backend = ""
            self._model = ""
            try:
                self._worker = await self._spawn(worker_command, environment=worker_environment, name="worker-" + backend)
                await self._wait_worker(backend, selected_model, self._worker)
                self._gateway = await self._start_gateway(backend)
            except Exception as exc:
                await self._stop_children()
                self._last_error = str(exc)
                raise SupervisorError("后端切换失败：%s" % exc) from exc
            self._backend = backend
            self._model = selected_model
            self._started_at = time.time()
            self._last_error = ""
            return self.status()

    def status(self) -> Mapping[str, Any]:
        return {
            "ready": bool(self._backend and self._worker is not None and self._gateway is not None),
            "backend": self._backend or None,
            "model": self._model or None,
            "started_at": self._started_at or None,
            "last_error": self._last_error or None,
            "backends": self.public_backends(),
        }

    async def close(self) -> None:
        async with self._transition_lock():
            await self._stop_children()


def create_app(supervisor: BackendSupervisor) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def require_loopback(request: Request) -> None:
        host = request.client.host if request.client else ""
        if host not in _LOOPBACK_CLIENTS:
            raise HTTPException(status_code=403, detail="backend supervisor only accepts loopback clients")

    @app.get("/health")
    async def health(request: Request) -> Mapping[str, Any]:
        require_loopback(request)
        return {"status": "ready", **supervisor.status()}

    @app.get("/api/status")
    async def status(request: Request) -> Mapping[str, Any]:
        require_loopback(request)
        return supervisor.status()

    @app.post("/api/select")
    async def select(request: Request, payload: SelectBackendRequest) -> Mapping[str, Any]:
        require_loopback(request)
        try:
            return await supervisor.select(payload.backend, payload.model)
        except (SupervisorError, CandidateError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.on_event("shutdown")
    async def shutdown_children() -> None:
        await supervisor.close()

    return app


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Ascend Edge Chat backend supervisor")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=7863, type=int)
    parser.add_argument("--candidate-data-dir", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CANDIDATE_CATALOG)
    parser.add_argument("--mindspore-catalog", type=Path, default=DEFAULT_CATALOG_PATH)
    args = parser.parse_args(argv)
    if args.host != "127.0.0.1":
        parser.error("backend supervisor must remain loopback-only")
    supervisor = BackendSupervisor(
        catalog_path=args.mindspore_catalog.resolve(),
        acl_catalog_path=args.catalog.resolve(),
        candidate_data_dir=args.candidate_data_dir.resolve(),
    )
    app = create_app(supervisor)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
