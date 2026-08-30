from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import time
import unittest

from case9_model_profiles import load_profiles, write_active_state


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "case9-modelctl.sh"


def _bash_path(path: Path) -> str:
    value = str(path.resolve()).replace("\\", "/")
    if len(value) >= 2 and value[1] == ":":
        return "/mnt/" + value[0].lower() + value[2:]
    return value


def _shell(command: str) -> list[str]:
    """Use wsl.exe on Windows; its legacy bash.exe shim loses `$!`."""

    if shutil.which("wsl.exe"):
        return ["wsl.exe", "-e", "bash", "-lc", command]
    return ["bash", "-lc", command]


def _bash_available() -> bool:
    return subprocess.call(
        _shell("command -v bash >/dev/null 2>&1"),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ) == 0


@unittest.skipUnless(_bash_available(), "bash unavailable")
class ModelCtlShellTests(unittest.TestCase):
    @staticmethod
    def _run(
        temp_root: Path,
        *arguments: str,
        allow_external: bool = True,
        **extra: str,
    ) -> subprocess.CompletedProcess[str]:
        # Explicitly use a WSL-side interpreter for non-interactive coverage;
        # no board or package manager is touched.
        values = {
            "CONDA_PROFILE": _bash_path(temp_root / "missing-conda.sh"),
            "PYTHON_BIN": "/usr/bin/python3",
            "CASE9_MODELCTL_STATE_DIR": _bash_path(temp_root / "state"),
            "CASE9_MODELCTL_LOG_DIR": _bash_path(temp_root / "logs"),
        }
        if allow_external:
            values["CASE9_MODELCTL_ALLOW_EXTERNAL_PYTHON"] = "1"
        values.update(extra)
        assignments = " ".join(
            "%s=%s" % (key, shlex.quote(value)) for key, value in values.items()
        )
        command = "%s bash %s %s" % (
            assignments,
            shlex.quote(_bash_path(SCRIPT)),
            " ".join(shlex.quote(argument) for argument in arguments),
        )
        return subprocess.run(
            _shell(command),
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    @staticmethod
    def _run_script(
        temp_root: Path,
        script_path: Path,
        *arguments: str,
        **extra: str,
    ) -> subprocess.CompletedProcess[str]:
        """Run a copied modelctl script with a fully isolated fake deployment."""

        values = {
            "CONDA_PROFILE": _bash_path(temp_root / "missing-conda.sh"),
            "PYTHON_BIN": "/usr/bin/python3",
            "CASE9_MODELCTL_ALLOW_EXTERNAL_PYTHON": "1",
            "CASE9_MODELCTL_STATE_DIR": _bash_path(temp_root / "state"),
            "CASE9_MODELCTL_LOG_DIR": _bash_path(temp_root / "logs"),
            "CASE9_MODEL_PROFILES": _bash_path(temp_root / "configs" / "chat_model_profiles.json"),
            "CASE9_ALLOW_EXPERIMENTAL": "1",
            "CASE9_MODELCTL_WAIT_SECONDS": "0",
            "CASE9_MODELCTL_STOP_SECONDS": "0",
        }
        values.update(extra)
        assignments = " ".join(
            "%s=%s" % (key, shlex.quote(value)) for key, value in values.items()
        )
        command = "%s bash %s %s" % (
            assignments,
            shlex.quote(_bash_path(script_path)),
            " ".join(shlex.quote(argument) for argument in arguments),
        )
        return subprocess.run(
            _shell(command),
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_shell_syntax_and_identity_guards_are_present(self) -> None:
        syntax_command = (
            ["wsl.exe", "-e", "bash", "-n", _bash_path(SCRIPT)]
            if shutil.which("wsl.exe")
            else ["bash", "-n", _bash_path(SCRIPT)]
        )
        result = subprocess.run(
            syntax_command,
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        source = SCRIPT.read_text(encoding="utf-8")
        launcher_source = (ROOT / "scripts" / "run_mindspore_chat_service.sh").read_text(encoding="utf-8")
        for required in (
            "Conda profile not found",
            "refusing system-Python fallback",
            "CASE9_MODELCTL_ALLOW_EXTERNAL_PYTHON",
            "worker PID ${pid} is still alive after KILL",
            "wait_ready \"${requested}\" \"${new_pid}\"",
            "write_state \"${previous_profile}\" \"${previous_pid}\" \"switching\"",
            "http_status",
            "worker_pid != expected_pid",
            "CASE9_ALLOW_EXPERIMENTAL",
            "preserve_failed_worker",
            'write_state "${profile}" "${pid}" "failed" "false"',
            '"stale"',
            'CASE9_PYTHON_BIN="${python_bin}"',
            "setsid",
            "worker_group_isolated",
            "wait_for_isolated_group",
            "starting.json",
            "write_starting_journal",
            "read_starting_journal",
            "recover_starting_journal",
            'write_state "${profile}" "${pid}" "starting" "false"',
            'worker_pgid',
            'starting worker leader is gone but PGID',
            'starting worker recovery failed; journal retained',
            "CASE9_MODELCTL_GROUP_WAIT_ATTEMPTS",
            "CASE9_MODELCTL_GROUP_WAIT_DELAY_SECONDS",
            "for ((attempt=0; attempt<attempts; attempt+=1))",
            '[[ "${pgid}" == "${pid}" && "${sid}" == "${pid}" ]]',
            'worker_pid_is_live "${pid}" || return 1',
            'pgid="$(wait_for_isolated_group "${pid}" 2>/dev/null || true)"',
            'kill -TERM -- "-${pgid}"',
            'kill -KILL -- "-${pgid}"',
            "worker_group_owned",
            "worker.pgid",
            "group_isolated and group_alive and health_ok",
            "preflight_tracking",
            "clear_consistent_sidecars",
            "unreconciled worker sidecars",
            "CASE9_MODELCTL_TEST_FAIL_JOURNAL_WRITE",
            "abort_launched_worker",
        ):
            self.assertIn(required, source)
        for required in (
            "worker must run as a setsid session/process-group leader",
            'worker_pgid=\"$(ps -p \"$$\" -o pgid=',
            'worker_sid=\"$(ps -p \"$$\" -o sid=',
        ):
            self.assertIn(required, launcher_source)

        # The sidecar must never be populated from the transient PGID seen
        # immediately after ``setsid ... &``.  Keep this ordering assertion
        # here because a shell integration test cannot deterministically force
        # that scheduler window on every host (and WSL skips native process
        # group tests).
        launch_start = source.index('CASE9_PROCESS_GROUP_READY="1"')
        wait_call = source.index(
            'pgid="$(wait_for_isolated_group "${pid}" 2>/dev/null || true)"',
            launch_start,
        )
        sidecar_write = source.index('write_pgid_file "${pid}" "${pgid}"', wait_call)
        self.assertLess(wait_call, sidecar_write)

        launch_start = source.index("launch_profile() {")
        starting_journal = source.index(
            'write_starting_journal "${profile}" "${pid}" "${pgid}" "${log_path}"',
            launch_start,
        )
        starting_state = source.index(
            'write_state "${profile}" "${pid}" "starting" "false"',
            starting_journal,
        )
        readiness = source.index('wait_ready "${requested}" "${new_pid}"', starting_journal)
        self.assertLess(starting_journal, readiness)
        self.assertLess(starting_journal, starting_state)

    def test_status_reports_an_unreconciled_starting_journal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            journal = root / "state" / "starting.json"
            journal.parent.mkdir(parents=True)
            journal.write_text("{}\n", encoding="utf-8")
            result = self._run(root, "status", CASE9_MODELCTL_STARTING_FILE=_bash_path(journal))
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["runtime"]["starting_journal_present"])

    def test_invalid_starting_journal_blocks_mutation_and_is_retained(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            journal = root / "state" / "starting.json"
            journal.parent.mkdir(parents=True)
            journal.write_text("{}\n", encoding="utf-8")
            result = self._run(
                root,
                "stop",
                CASE9_MODELCTL_STARTING_FILE=_bash_path(journal),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("starting worker recovery failed", result.stderr)
            self.assertTrue(journal.is_file())

    def test_starting_journal_directory_blocks_mutation(self) -> None:
        """A non-file journal path is occupied and must fail closed."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            journal = root / "state" / "starting.json"
            journal.mkdir(parents=True)
            result = self._run(
                root,
                "stop",
                CASE9_MODELCTL_STARTING_FILE=_bash_path(journal),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("starting worker recovery failed", result.stderr)
            self.assertTrue(journal.is_dir())

    def test_starting_journal_retained_when_process_table_is_unreadable(self) -> None:
        """A failed ``ps`` probe must not be treated as a dead worker.

        ``worker_pid_is_live`` intentionally returns ``2`` when the process
        table cannot be inspected.  The recovery path must retain its journal
        in that case instead of clearing it and permitting an unsafe switch.
        """

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / "state"
            log_dir = root / "logs"
            shim_dir = root / "bin"
            state_dir.mkdir()
            log_dir.mkdir()
            shim_dir.mkdir()
            journal = state_dir / "starting.json"
            journal.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "status": "starting",
                        "profile_id": "qwen1.5-0.5b-mindspore",
                        # PID 1 is present in both native POSIX and WSL test
                        # shells, so kill -0 reaches the ps error branch.
                        "worker_pid": 1,
                        "worker_pgid": 1,
                        "health_port": 8090,
                        "log_path": _bash_path(log_dir / "worker.log"),
                        "created_at": "2026-08-30T00:00:00Z",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            ps_shim = shim_dir / "ps"
            ps_shim.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
            ps_shim.chmod(0o755)

            # Keep a real WSL process alive so ``kill -0`` succeeds before
            # the deliberately failing ``ps`` probe is reached.
            worker = subprocess.Popen(
                _shell("sleep 60 & echo $!; wait"),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
            worker_pid = int(worker.stdout.readline().strip())

            try:
                payload = json.loads(journal.read_text(encoding="utf-8"))
                payload["worker_pid"] = worker_pid
                payload["worker_pgid"] = worker_pid
                journal.write_text(json.dumps(payload) + "\n", encoding="utf-8")
                result = self._run(
                    root,
                    "stop",
                    CASE9_MODELCTL_STARTING_FILE=_bash_path(journal),
                    PATH=_bash_path(shim_dir) + ":/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("could not inspect starting worker PID", result.stderr)
                self.assertTrue(journal.is_file())
            finally:
                subprocess.run(
                    _shell("kill -TERM %d 2>/dev/null || true" % worker_pid),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
                worker.terminate()
                try:
                    worker.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    worker.kill()
                    worker.wait(timeout=3)

    def test_leader_dead_descendant_path_remains_fail_closed(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        start = source.index("stop_pid() {")
        end = source.index("\nhealth_ready() {", start)
        stop_source = source[start:end]
        self.assertIn("worker leader ${pid} is gone but PGID ${pgid} still has live children", stop_source)
        # A leader that disappears is not enough evidence to signal its old
        # group.  The only later KILL path is guarded by worker_group_owned,
        # which verifies every remaining member's PGID/SID before signaling.
        dead_leader_branch = stop_source.index("if worker_pid_is_live \"${pid}\"; then")
        identity_guard = stop_source.index("pid_matches_worker \"${pid}\"", dead_leader_branch)
        self.assertLess(dead_leader_branch, identity_guard)
        kill_guard = stop_source.index("if worker_group_owned \"${pgid}\"", identity_guard)
        kill_call = stop_source.index('kill -KILL -- "-${pgid}"', kill_guard)
        self.assertLess(kill_guard, kill_call)

    def test_controller_crash_leaves_starting_journal_for_safe_stop(self) -> None:
        """A controller exit after launch must not strand the candidate.

        The fixture uses a long-lived, identity-matching worker and kills the
        modelctl wrapper after its atomic journal appears.  A fresh ``stop``
        invocation then consumes that journal and terminates only the isolated
        worker group.
        """

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir(parents=True)
            (root / "configs").mkdir(parents=True)
            shutil.copy2(SCRIPT, root / "scripts" / SCRIPT.name)
            shutil.copy2(ROOT / "case9_model_profiles.py", root / "case9_model_profiles.py")
            shutil.copy2(
                ROOT / "configs" / "chat_model_profiles.json",
                root / "configs" / "chat_model_profiles.json",
            )
            fake_launcher = root / "scripts" / "run_mindspore_chat_service.sh"
            fake_launcher.write_text(
                "#!/usr/bin/env bash\n"
                "exec -a 'python mindspore_chat_service.py --profile qwen1.5-0.5b-mindspore --port 8090' "
                "bash -c 'trap : TERM INT; sleep 600'\n",
                encoding="utf-8",
            )
            state_dir = root / "state"
            journal = state_dir / "starting.json"
            values = {
                "CONDA_PROFILE": _bash_path(root / "missing-conda.sh"),
                "PYTHON_BIN": "/usr/bin/python3",
                "CASE9_MODELCTL_ALLOW_EXTERNAL_PYTHON": "1",
                "CASE9_MODELCTL_STATE_DIR": _bash_path(state_dir),
                "CASE9_MODELCTL_LOG_DIR": _bash_path(root / "logs"),
                "CASE9_MODEL_PROFILES": _bash_path(root / "configs" / "chat_model_profiles.json"),
                "CASE9_MODELCTL_STARTING_FILE": _bash_path(journal),
                "CASE9_ALLOW_EXPERIMENTAL": "1",
                # Keep the crashed controller in its readiness loop until the
                # test has observed the journal.
                "CASE9_MODELCTL_WAIT_SECONDS": "60",
                "CASE9_MODELCTL_STOP_SECONDS": "0",
            }
            assignments = " ".join(
                "%s=%s" % (key, shlex.quote(value)) for key, value in values.items()
            )
            command = "%s bash %s switch qwen1.5-0.5b-mindspore" % (
                assignments,
                shlex.quote(_bash_path(root / "scripts" / SCRIPT.name)),
            )
            controller = subprocess.Popen(
                _shell(command),
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            worker_pid = None
            try:
                deadline = time.monotonic() + 8.0
                while time.monotonic() < deadline and not journal.exists():
                    if controller.poll() is not None:
                        break
                    time.sleep(0.02)
                self.assertTrue(journal.is_file(), "starting journal was not written")
                journal_payload = json.loads(journal.read_text(encoding="utf-8"))
                worker_pid = int(journal_payload["worker_pid"])
                # The journal is intentionally written before its mirror
                # files.  Wait for that documented atomic-write boundary so
                # this test exercises controller loss after a complete
                # tracking record, rather than interrupting a writer midway
                # and manufacturing a `.part` orphan.
                pid_sidecar = state_dir / "worker.pid"
                pgid_sidecar = state_dir / "worker.pgid"
                state_path = state_dir / "active-model.json"
                deadline = time.monotonic() + 5.0
                while time.monotonic() < deadline:
                    temporary = list(state_dir.glob("*.part.*"))
                    if pid_sidecar.is_file() and pgid_sidecar.is_file() and state_path.is_file() and not temporary:
                        break
                    time.sleep(0.02)
                self.assertTrue(pid_sidecar.is_file(), "PID sidecar was not written")
                self.assertTrue(pgid_sidecar.is_file(), "PGID sidecar was not written")
                self.assertTrue(state_path.is_file(), "active state was not written")
                self.assertFalse(list(state_dir.glob("*.part.*")), "atomic writer still has a temporary file")
                controller.kill()
                controller.wait(timeout=5)

                stop_result = self._run_script(
                    root,
                    root / "scripts" / SCRIPT.name,
                    "stop",
                    CASE9_MODELCTL_STARTING_FILE=_bash_path(journal),
                    # Give the trapped shell a bounded chance to reap its
                    # TERM'd child before the controller rechecks the group.
                    # A zero-second fixture races that handoff and can reach
                    # the deliberately fail-closed ps-error path.
                    CASE9_MODELCTL_STOP_SECONDS="1",
                )
                self.assertEqual(stop_result.returncode, 0, stop_result.stderr)
                self.assertFalse(journal.exists())
                self.assertFalse((state_dir / "active-model.json").exists())
                self.assertFalse((state_dir / "worker.pid").exists())
                self.assertFalse((state_dir / "worker.pgid").exists())
            finally:
                if controller.poll() is None:
                    controller.kill()
                    try:
                        controller.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        controller.terminate()
                if journal.exists() or worker_pid is not None:
                    # Best-effort fixture cleanup.  The same recovery path is
                    # exercised even when an assertion above fails.
                    self._run_script(
                        root,
                        root / "scripts" / SCRIPT.name,
                        "stop",
                        CASE9_MODELCTL_STARTING_FILE=_bash_path(journal),
                        CASE9_MODELCTL_STOP_SECONDS="0",
                    )

    def test_health_ready_rechecks_isolated_live_process_group(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        start = source.index("health_ready() {")
        end = source.index("\nwait_ready() {", start)
        health_source = source[start:end]
        self.assertIn('expected_pgid="$(worker_group_id "${expected_pid}"', health_source)
        self.assertIn('worker_group_isolated "${expected_pid}" "${expected_pgid}" || return 1', health_source)
        self.assertIn('worker_group_alive "${expected_pgid}" || return 1', health_source)
        self.assertIn('re.fullmatch(r"[0-9a-fA-F]{64}", fingerprint)', health_source)

    def test_missing_conda_refuses_system_python_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = self._run(Path(directory), "list", allow_external=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing system-Python fallback", result.stderr)

    def test_explicit_external_python_requires_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = self._run(Path(directory), "list", allow_external=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"profiles"', result.stdout)

    def test_status_without_state_reports_empty_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = self._run(Path(directory), "status")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertIsNone(payload["active"])
        self.assertEqual(payload["runtime"]["state_status"], "none")
        self.assertFalse(payload["runtime"]["stale"])
        self.assertFalse(payload["runtime"]["sidecars_present"])
        self.assertFalse(payload["runtime"]["orphan_recovery_required"])

    def test_status_reports_complete_orphan_sidecars_as_stale(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / "state"
            state_dir.mkdir()
            (state_dir / "worker.pid").write_text("99999999\n", encoding="utf-8")
            (state_dir / "worker.pgid").write_text("99999999\n", encoding="utf-8")
            result = self._run(root, "status")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        runtime = payload["runtime"]
        self.assertIsNone(payload["active"])
        self.assertTrue(runtime["sidecars_present"])
        self.assertEqual(runtime["sidecar_status"], "complete")
        self.assertTrue(runtime["orphan_recovery_required"])
        self.assertTrue(runtime["stale"])

    def test_orphan_sidecar_shapes_fail_closed_and_are_retained(self) -> None:
        cases = (
            ("pid-only", "pid", "12345\n"),
            ("pgid-only", "pgid", "12345\n"),
            ("pid-malformed", "both-pid", "12 34\n"),
            ("pgid-malformed", "both-pgid", "0\n"),
            ("numeric-mismatch", "mismatch", "12345\n"),
        )
        for name, shape, value in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                state_dir = root / "state"
                state_dir.mkdir()
                pid_path = state_dir / "worker.pid"
                pgid_path = state_dir / "worker.pgid"
                if shape in {"pid", "both-pid", "both-pgid", "mismatch"}:
                    pid_path.write_text(value if shape == "both-pid" else "12345\n", encoding="utf-8")
                if shape in {"pgid", "both-pid", "both-pgid", "mismatch"}:
                    pgid_path.write_text(
                        value if shape == "both-pgid" else ("12346\n" if shape == "mismatch" else "12345\n"),
                        encoding="utf-8",
                    )
                status = self._run(root, "status")
                self.assertEqual(status.returncode, 0, status.stderr)
                runtime = json.loads(status.stdout)["runtime"]
                self.assertTrue(runtime["sidecars_present"])
                self.assertNotEqual(runtime["sidecar_status"], "complete")
                self.assertTrue(runtime["stale"])
                stop = self._run(root, "stop")
                self.assertNotEqual(stop.returncode, 0)
                if shape in {"pid", "both-pid", "both-pgid", "mismatch"}:
                    self.assertTrue(pid_path.exists() or pid_path.is_symlink())
                if shape in {"pgid", "both-pid", "both-pgid", "mismatch"}:
                    self.assertTrue(pgid_path.exists() or pgid_path.is_symlink())

    def test_orphan_symlink_sidecar_is_not_followed_or_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / "state"
            state_dir.mkdir()
            target = root / "outside-target"
            target.write_text("99999999\n", encoding="utf-8")
            link = state_dir / "worker.pid"
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation is unavailable on this controller")
            (state_dir / "worker.pgid").write_text("99999999\n", encoding="utf-8")
            result = self._run(root, "stop")
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(link.is_symlink())
            self.assertTrue(target.is_file())

    def test_dead_complete_orphan_sidecars_are_cleaned_only_when_group_empty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / "state"
            state_dir.mkdir()
            pid_path = state_dir / "worker.pid"
            pgid_path = state_dir / "worker.pgid"
            pid_path.write_text("99999999\n", encoding="utf-8")
            pgid_path.write_text("99999999\n", encoding="utf-8")
            result = self._run(root, "stop")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(pid_path.exists())
        self.assertFalse(pgid_path.exists())

    def test_state_and_complete_sidecars_must_point_to_the_same_pid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_dir = root / "state"
            state_path = state_dir / "active-model.json"
            write_active_state(
                state_path,
                "qwen1.5-0.5b-mindspore",
                status="failed",
                worker_pid=99999999,
                cache_cleared=False,
                registry=load_profiles(),
            )
            (state_dir / "worker.pid").write_text("99999998\n", encoding="utf-8")
            (state_dir / "worker.pgid").write_text("99999998\n", encoding="utf-8")
            result = self._run(root, "stop")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("inconsistent", result.stderr)
            self.assertTrue(state_path.is_file())
            self.assertTrue((state_dir / "worker.pid").is_file())
            self.assertTrue((state_dir / "worker.pgid").is_file())

    @unittest.skipUnless(
        os.name == "posix" and not shutil.which("wsl.exe"),
        "requires a native POSIX process table",
    )
    def test_live_complete_orphan_sidecars_without_profile_are_not_signalled(self) -> None:
        profile = "qwen1.5-0.5b-mindspore"
        argv0 = "python mindspore_chat_service.py --profile %s --port 8090" % profile
        worker = subprocess.Popen(
            ["bash", "-c", "exec -a %s sleep 60" % shlex.quote(argv0)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                state_dir = root / "state"
                state_dir.mkdir()
                (state_dir / "worker.pid").write_text("%d\n" % worker.pid, encoding="utf-8")
                (state_dir / "worker.pgid").write_text("%d\n" % worker.pid, encoding="utf-8")
                result = self._run(root, "stop", CASE9_MODELCTL_STOP_SECONDS="0")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("no state/journal identity", result.stderr)
                self.assertIsNone(worker.poll())
                self.assertTrue((state_dir / "worker.pid").is_file())
                self.assertTrue((state_dir / "worker.pgid").is_file())
        finally:
            if worker.poll() is None:
                worker.terminate()
                try:
                    worker.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    worker.kill()
                    worker.wait(timeout=3)

    def test_launch_persistence_failures_stop_worker_or_retain_pointer(self) -> None:
        """A failed tracking write must never return an untracked live worker."""

        failure_flags = (
            "CASE9_MODELCTL_TEST_FAIL_JOURNAL_WRITE",
            "CASE9_MODELCTL_TEST_FAIL_PGID_WRITE",
            "CASE9_MODELCTL_TEST_FAIL_PID_WRITE",
            "CASE9_MODELCTL_TEST_FAIL_STATE_WRITE",
        )
        for failure_flag in failure_flags:
            with self.subTest(failure_flag=failure_flag), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "scripts").mkdir(parents=True)
                (root / "configs").mkdir(parents=True)
                shutil.copy2(SCRIPT, root / "scripts" / SCRIPT.name)
                shutil.copy2(ROOT / "case9_model_profiles.py", root / "case9_model_profiles.py")
                shutil.copy2(
                    ROOT / "configs" / "chat_model_profiles.json",
                    root / "configs" / "chat_model_profiles.json",
                )
                worker_pid_path = root / "worker.pid.observed"
                fake_launcher = root / "scripts" / "run_mindspore_chat_service.sh"
                fake_launcher.write_text(
                    "#!/usr/bin/env bash\n"
                    "printf '%s\\n' \"$$\" > \"${CASE9_TEST_WORKER_PID_FILE}\"\n"
                    "exec -a 'python mindspore_chat_service.py --profile qwen1.5-0.5b-mindspore --port 8090' "
                    "bash -c 'trap : TERM INT; sleep 60'\n",
                    encoding="utf-8",
                )
                result = self._run_script(
                    root,
                    root / "scripts" / SCRIPT.name,
                    "switch",
                    "qwen1.5-0.5b-mindspore",
                    CASE9_TEST_WORKER_PID_FILE=_bash_path(worker_pid_path),
                    **{failure_flag: "1"},
                    CASE9_MODELCTL_STOP_SECONDS="1",
                )
                self.assertNotEqual(result.returncode, 0, result.stderr)
                # A persistence failure may be handled before the fixture's
                # launcher gets scheduled to write its diagnostic PID.  That
                # is safe: the implementation already owns `$!` and can stop
                # the worker directly.  When the diagnostic file exists, use
                # it to assert the process was not left running.
                worker_pid = None
                if worker_pid_path.is_file():
                    worker_pid = int(worker_pid_path.read_text(encoding="utf-8").strip())

                def process_is_live(pid: int) -> bool:
                    probe = subprocess.run(
                        _shell("ps -p %d -o stat=" % pid),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        text=True,
                        check=False,
                    )
                    states = [line.strip() for line in probe.stdout.splitlines() if line.strip()]
                    return bool(states) and not states[0].startswith("Z")

                if worker_pid is not None:
                    deadline = time.monotonic() + 5.0
                    while time.monotonic() < deadline and process_is_live(worker_pid):
                        time.sleep(0.05)
                    self.assertFalse(process_is_live(worker_pid), result.stderr)

                state_dir = root / "state"
                tracked = [
                    path
                    for path in (
                        state_dir / "active-model.json",
                        state_dir / "starting.json",
                        state_dir / "worker.pid",
                        state_dir / "worker.pgid",
                    )
                    if path.exists() or path.is_symlink()
                ]
                # Either outcome is valid: a fully stopped worker permits all
                # metadata to be removed, while an unsafe stop must retain a
                # pointer for later diagnosis.  The important invariant is
                # that launch never reports success with a live, untracked
                # candidate.  A PID-write fault commonly leaves journal/PGID
                # metadata; verify a retry does not launch over it.
                if tracked:
                    retry = self._run_script(root, root / "scripts" / SCRIPT.name, "stop")
                    self.assertNotEqual(retry.returncode, 0)

    def test_status_marks_dead_worker_as_stale(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "state" / "active-model.json"
            write_active_state(
                state_path,
                "qwen1.5-0.5b-mindspore",
                status="running",
                worker_pid=99999999,
                cache_cleared=True,
                registry=load_profiles(),
            )
            result = self._run(root, "status")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["runtime"]["worker_pid"], 99999999)
        self.assertFalse(payload["runtime"]["pid_alive"])
        self.assertFalse(payload["runtime"]["identity_match"])
        self.assertTrue(payload["runtime"]["stale"])

    def test_invalid_health_port_is_rejected_before_state_access(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = self._run(Path(directory), "list", MINDSPORE_CHAT_PORT="0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("MINDSPORE_CHAT_PORT", result.stderr)

    def test_blocked_profile_cannot_be_switched(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = self._run(
                Path(directory),
                "switch",
                "deepseek-r1-qwen-1.5b-mindspore",
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("profile is blocked", result.stderr)
        self.assertNotIn("profile is not present in registry", result.stderr)

    def test_tinyllama_failed_profile_cannot_be_switched(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = self._run(
                Path(directory),
                "switch",
                "tinyllama-1.1b-mindspore",
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("profile is blocked", result.stderr)
        self.assertNotIn("profile is not present in registry", result.stderr)

    def test_unknown_profile_has_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = self._run(Path(directory), "switch", "does-not-exist")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("profile is not present in registry", result.stderr)

    def test_failed_candidate_stop_persists_candidate_state(self) -> None:
        """A candidate that cannot be safely stopped remains recoverable.

        The fake launcher intentionally starts a process whose command line does
        not match the worker identity guard.  This exercises the exact branch
        where ``stop_pid`` refuses to terminate the candidate, without touching
        a board or a real model process.
        """

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir(parents=True)
            (root / "configs").mkdir(parents=True)
            shutil.copy2(SCRIPT, root / "scripts" / SCRIPT.name)
            shutil.copy2(ROOT / "case9_model_profiles.py", root / "case9_model_profiles.py")
            shutil.copy2(
                ROOT / "configs" / "chat_model_profiles.json",
                root / "configs" / "chat_model_profiles.json",
            )
            fake_launcher = root / "scripts" / "run_mindspore_chat_service.sh"
            fake_launcher.write_text(
                # Keep the intentionally mismatched process alive long enough
                # for the immediate readiness check to reach stop/preserve.
                "#!/usr/bin/env bash\nexec -a failed-candidate sleep 600\n",
                encoding="utf-8",
            )
            result = self._run_script(
                root,
                root / "scripts" / SCRIPT.name,
                "switch",
                "qwen1.5-0.5b-mindspore",
                CASE9_MODELCTL_WAIT_SECONDS="0",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("failed worker state retained", result.stderr)
            state_path = root / "state" / "active-model.json"
            pid_path = root / "state" / "worker.pid"
            self.assertTrue(state_path.is_file())
            self.assertTrue(pid_path.is_file())
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["profile_id"], "qwen1.5-0.5b-mindspore")
            self.assertEqual(state["status"], "failed")
            self.assertFalse(state["cache_cleared"])
            self.assertEqual(int(pid_path.read_text(encoding="utf-8").strip()), state["worker_pid"])
            status_result = self._run_script(root, root / "scripts" / SCRIPT.name, "status")
            self.assertEqual(status_result.returncode, 0, status_result.stderr)
            status_payload = json.loads(status_result.stdout)
            self.assertTrue(status_payload["runtime"]["stale"])
            self.assertEqual(status_payload["runtime"]["worker_pid"], state["worker_pid"])
            subprocess.run(
                _shell("kill -KILL %d 2>/dev/null || true" % state["worker_pid"]),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )

    @unittest.skipUnless(
        os.name == "posix" and not shutil.which("wsl.exe"),
        "requires a native POSIX process table",
    )
    def test_identity_mismatch_preserves_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worker = subprocess.Popen(
                ["bash", "-c", "exec -a unrelated-worker sleep 60"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                state_path = root / "state" / "active-model.json"
                write_active_state(
                    state_path,
                    "qwen1.5-0.5b-mindspore",
                    worker_pid=worker.pid,
                    cache_cleared=True,
                    registry=load_profiles(),
                )
                result = self._run(root, "stop")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("identity mismatch", result.stderr)
                self.assertTrue(state_path.is_file())
            finally:
                worker.terminate()
                try:
                    worker.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    worker.kill()
                    worker.wait(timeout=3)

    @unittest.skipUnless(
        os.name == "posix" and not shutil.which("wsl.exe"),
        "requires a native POSIX process table",
    )
    def test_matching_worker_is_stopped_before_state_clear(self) -> None:
        profile = "qwen1.5-0.5b-mindspore"
        argv0 = "python mindspore_chat_service.py --profile %s --port 8090" % profile
        worker = subprocess.Popen(
            ["bash", "-c", "exec -a %s sleep 60" % shlex.quote(argv0)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            # Match the launcher's setsid contract: modelctl must only signal
            # a worker that owns its own session/process group.
            start_new_session=True,
        )
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                state_path = root / "state" / "active-model.json"
                write_active_state(
                    state_path,
                    profile,
                    worker_pid=worker.pid,
                    cache_cleared=True,
                    registry=load_profiles(),
                )
                result = self._run(root, "stop", CASE9_MODELCTL_STOP_SECONDS="0")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(state_path.exists())
                self.assertIsNotNone(worker.poll())
        finally:
            if worker.poll() is None:
                worker.terminate()
                try:
                    worker.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    worker.kill()
                    worker.wait(timeout=3)

    @unittest.skipUnless(
        os.name == "posix" and not shutil.which("wsl.exe"),
        "requires a native POSIX process table",
    )
    def test_process_group_stop_removes_multiprocessing_child(self) -> None:
        """A controlled stop must terminate a worker's isolated descendants."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir(parents=True)
            (root / "configs").mkdir(parents=True)
            shutil.copy2(SCRIPT, root / "scripts" / SCRIPT.name)
            shutil.copy2(ROOT / "case9_model_profiles.py", root / "case9_model_profiles.py")
            shutil.copy2(
                ROOT / "configs" / "chat_model_profiles.json",
                root / "configs" / "chat_model_profiles.json",
            )
            child_pid_file = root / "child.pid"
            fake_launcher = root / "scripts" / "run_mindspore_chat_service.sh"
            fake_launcher.write_text(
                "#!/usr/bin/env bash\n"
                "set -e\n"
                "sleep 600 &\n"
                "child=$!\n"
                "printf '%s\\n' \"$child\" > \"${CASE9_CHILD_PID_FILE}\"\n"
                # Keep a shell leader (the TERM trap prevents bash's exec
                # optimisation from replacing it with `sleep`) so its argv
                # remains suitable for the identity guard while the child
                # exercises process-group cleanup.
                "exec -a 'python mindspore_chat_service.py --profile qwen1.5-0.5b-mindspore --port 8090' bash -c 'trap : TERM INT; sleep 600'\n",
                encoding="utf-8",
            )
            result = self._run_script(
                root,
                root / "scripts" / SCRIPT.name,
                "switch",
                "qwen1.5-0.5b-mindspore",
                CASE9_CHILD_PID_FILE=_bash_path(child_pid_file),
                CASE9_MODELCTL_WAIT_SECONDS="1",
            )
            self.assertNotEqual(result.returncode, 0, result.stderr)
            self.assertTrue(child_pid_file.is_file(), result.stderr)
            child_pid = int(child_pid_file.read_text(encoding="utf-8").strip())

            def process_is_live(pid: int) -> bool:
                probe = subprocess.run(
                    ["ps", "-p", str(pid), "-o", "stat="],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    check=False,
                )
                if probe.returncode != 0:
                    return False
                states = [line.strip() for line in probe.stdout.splitlines() if line.strip()]
                return bool(states) and not states[0].startswith("Z")

            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and process_is_live(child_pid):
                time.sleep(0.05)
            self.assertFalse(process_is_live(child_pid))


if __name__ == "__main__":
    unittest.main()
