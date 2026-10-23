"""Independent board-side voice-agent core for Ascend Edge Chat.

Importing :mod:`ascend_edge_chat.voice` does not import FastAPI, open ALSA
hardware, load ASR/TTS models, or contact the text gateway.  The web service is
loaded lazily only when ``VoiceAgent`` or ``create_voice_agent_app`` is used.
"""

from .alsa import AlsaAudioIO, AlsaPcmDevice, discover_voice_devices, enumerate_alsa_devices
from .config import VoiceConfigurationError, VoiceSettings
from .protocol import VoiceAgentState, VoiceDevice, VoiceMode, VoiceTurn, VoiceTurnState
from .vad import EnergyVad, VadDecision, VadSettings, VadState


def __getattr__(name: str):
    """Load FastAPI-bound service symbols only when explicitly requested."""

    if name in {"VoiceAgent", "create_voice_agent_app"}:
        from .agent import VoiceAgent, create_voice_agent_app

        return {"VoiceAgent": VoiceAgent, "create_voice_agent_app": create_voice_agent_app}[name]
    raise AttributeError(name)


__all__ = [
    "AlsaAudioIO",
    "AlsaPcmDevice",
    "EnergyVad",
    "VadDecision",
    "VadSettings",
    "VadState",
    "VoiceAgent",
    "VoiceAgentState",
    "VoiceConfigurationError",
    "VoiceDevice",
    "VoiceMode",
    "VoiceSettings",
    "VoiceTurn",
    "VoiceTurnState",
    "create_voice_agent_app",
    "discover_voice_devices",
    "enumerate_alsa_devices",
]
