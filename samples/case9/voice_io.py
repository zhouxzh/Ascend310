"""
Voice input / output for the chatbot.

Speech recognition uses the speech_recognition library (Google Web Speech
API, free, no key needed).  TTS uses pyttsx3 (offline espeak backend).
Both operations run in background threads to avoid blocking.
"""

import threading
from config import VOICE_LANGUAGE, TTS_RATE, TTS_VOLUME


class SpeechRecognizer:
    """Wraps speech_recognition for single-shot and continuous listening."""

    def __init__(self, language=None):
        self._language = language or VOICE_LANGUAGE
        self._recognizer = None
        self._microphone = None
        self._listening = False
        self._thread = None

    def _get_recognizer(self):
        if self._recognizer is None:
            import speech_recognition as sr
            self._recognizer = sr.Recognizer()
        return self._recognizer

    def _get_microphone(self):
        if self._microphone is None:
            import speech_recognition as sr
            self._microphone = sr.Microphone()
        return self._microphone

    # -- single-shot ---------------------------------------------------------

    def recognize_from_microphone(self, timeout=5):
        """Blocking single-shot recognition. Returns text or None."""
        import speech_recognition as sr

        r = self._get_recognizer()
        mic = self._get_microphone()

        try:
            with mic as source:
                r.adjust_for_ambient_noise(source, duration=0.5)
                audio = r.listen(source, timeout=timeout, phrase_time_limit=8)
        except sr.WaitTimeoutError:
            return None
        except OSError:
            return "[错误] 找不到麦克风设备"

        try:
            return r.recognize_google(audio, language=self._language)
        except sr.UnknownValueError:
            return None
        except sr.RequestError as e:
            return f"[错误] 语音识别服务不可用: {e}"

    def recognize_from_file(self, audio_path):
        """Recognize speech from an audio file. Returns text."""
        import speech_recognition as sr

        r = self._get_recognizer()
        try:
            with sr.AudioFile(audio_path) as source:
                audio = r.record(source)
            return r.recognize_google(audio, language=self._language)
        except sr.UnknownValueError:
            return None
        except Exception as e:
            return f"[错误] {e}"

    # -- continuous ----------------------------------------------------------

    def start_listening(self, callback):
        """Start continuous background listening. callback(text) on each
        recognized phrase."""
        if self._listening:
            return
        self._listening = True
        self._thread = threading.Thread(
            target=self._listen_loop, args=(callback,), daemon=True
        )
        self._thread.start()

    def _listen_loop(self, callback):
        import speech_recognition as sr

        r = self._get_recognizer()
        mic = self._get_microphone()

        with mic as source:
            r.adjust_for_ambient_noise(source, duration=0.5)

        while self._listening:
            try:
                with mic as source:
                    audio = r.listen(source, timeout=0.5, phrase_time_limit=5)
                text = r.recognize_google(audio, language=self._language)
                if text:
                    callback(text)
            except sr.WaitTimeoutError:
                continue
            except sr.UnknownValueError:
                continue
            except sr.RequestError as e:
                callback(f"[语音识别错误: {e}]")
                break

    def stop_listening(self):
        self._listening = False
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None


class TextToSpeech:
    """Wraps pyttsx3 for offline TTS. Speaks in a background thread."""

    def __init__(self, rate=None, volume=None):
        self._rate = rate or TTS_RATE
        self._volume = volume or TTS_VOLUME
        self._engine = None
        self._speaking = False

    def _get_engine(self):
        if self._engine is None:
            import pyttsx3
            self._engine = pyttsx3.init()
            self._configure()
        return self._engine

    def _configure(self):
        self._engine.setProperty("rate", self._rate)
        self._engine.setProperty("volume", self._volume)
        # Try to select a Chinese-compatible voice
        voices = self._engine.getProperty("voices")
        for voice in voices:
            name_lower = (voice.name or "").lower()
            if any(tag in name_lower for tag in ("chinese", "zh", "mandarin", "cmn")):
                self._engine.setProperty("voice", voice.id)
                break

    def speak(self, text):
        """Speak text asynchronously in a background thread."""
        if self._speaking:
            self.stop()

        def _run():
            self._speaking = True
            try:
                engine = self._get_engine()
                engine.say(text)
                engine.runAndWait()
            finally:
                self._speaking = False

        t = threading.Thread(target=_run, daemon=True)
        t.start()

    def stop(self):
        try:
            if self._engine:
                self._engine.stop()
        except Exception:
            pass
        self._speaking = False

    @property
    def is_speaking(self):
        return self._speaking
