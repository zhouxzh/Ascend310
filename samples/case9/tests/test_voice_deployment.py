from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
import unittest

from ascend_edge_chat.cli import main


class VoiceDeploymentTests(unittest.TestCase):
    def test_voice_cli_delegates_only_loopback_arguments(self) -> None:
        with patch("ascend_edge_chat.voice.agent.main", return_value=0) as voice_main:
            result = main(
                [
                    "voice",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "7862",
                    "--check-config",
                ]
            )
        self.assertEqual(result, 0)
        voice_main.assert_called_once_with(
            ["--host", "127.0.0.1", "--port", "7862", "--check-config"]
        )

    def test_release_scripts_load_project_env_before_requirements(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for relative in (
            "scripts/run/start-worker.sh",
            "scripts/run/start-gateway.sh",
            "scripts/run/start-ui.sh",
            "scripts/run/start-voice-agent.sh",
            "scripts/verify/voice-preflight.sh",
            "scripts/verify/release-preflight.sh",
        ):
            script = (root / relative).read_text(encoding="utf-8")
            self.assertIn('env_file="${ASCEND_EDGE_CHAT_ENV_FILE:-${root_dir}/.env}"', script)
            self.assertIn('source "${env_file}"', script)

    def test_voice_start_script_isolated_from_text_and_cann_environment(self) -> None:
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "run" / "start-voice-agent.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("ASCEND_EDGE_CHAT_VOICE_CONDA_ENV", script)
        self.assertIn("PYTHONNOUSERSITE=1", script)
        self.assertIn("python -m ascend_edge_chat voice", script)
        self.assertNotIn("set_env.sh", script)
        self.assertNotIn("sudo", script)

    def test_voice_preflight_requires_devices_and_audio_access_without_mutation(self) -> None:
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "verify" / "voice-preflight.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("ASCEND_EDGE_CHAT_VOICE_CONDA_ENV", script)
        self.assertIn('[[ -r "${node}" ]]', script)
        self.assertIn('[[ -w "${node}" ]]', script)
        self.assertIn("/proc/asound/pcm", script)
        self.assertIn("python -m ascend_edge_chat voice --check-config", script)
        self.assertNotIn("usermod", script)
        self.assertNotIn("arecord -D", script)
        self.assertNotIn("aplay -D", script)

    def test_release_template_disables_voice_by_default(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "configs" / "release.env.example").read_text(encoding="utf-8")
        self.assertIn("ASCEND_EDGE_CHAT_VOICE_ENABLED=false", text)
        self.assertIn("ASCEND_EDGE_CHAT_VOICE_MODE=disabled", text)
        self.assertIn("ASCEND_EDGE_CHAT_VOICE_AGENT_URL=http://127.0.0.1:7862", text)
        self.assertNotIn("ASCEND_EDGE_CHAT_VOICE_OPERATOR_TOKEN=", text)
        self.assertNotIn("ASCEND_EDGE_CHAT_VOICE_UI_OPERATOR_TOKEN=", text)
        self.assertNotIn("TORCH", text)


if __name__ == "__main__":
    unittest.main()
