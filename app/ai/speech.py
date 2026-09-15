# -*- coding: utf-8 -*-
"""
Server-side speech-to-text.

WHY THIS EXISTS AT ALL
----------------------
The first version of this project did voice input with the browser's
`SpeechRecognition` API. That was a poor choice for this specific problem, and
it is worth being precise about why, because the reasons are the same reasons
the platform exists:

* It is Chrome/Safari only. Firefox has no implementation, so the mic is simply
  dead there.
* It requires a secure context. Open the app from a phone at
  `http://192.168.x.x:8000` -- exactly how you would demo it on real hardware --
  and the browser blocks it outright. This is the single most likely reason
  voice "doesn't work" for anyone who has tried it on a handset.
* Chrome's implementation ships the audio to Google and supports Google's
  language list. The low-resource Indic and African languages this platform is
  built to hear are precisely the ones it covers worst or not at all.

So browser recognition is a convenience, not the intake path. Real intake
transcribes server-side, where the model is a deployment decision rather than
whatever the citizen's browser happens to ship.

PROVIDERS
---------
`xvoice` -- the slot for XVoice (xvoicekeyboard.com). NOT yet verified against
    the real service: its request/response contract has not been confirmed, so
    what is implemented here is the common convention (multipart `file`, bearer
    auth, JSON `{"text": ...}`) driven entirely by environment variables. If
    XVoice differs, only this class changes. If XVoice is an on-device Android
    keyboard rather than an HTTP service, then no server adapter is wanted at
    all -- the citizen's own keyboard types into the form and the correct work
    is on the text field, not here. See docs/VOICE.md.

`groq` -- Whisper large v3 / turbo on Groq, reachable with the same
    GROQ_API_KEY already used for classification. ~99 languages, far better
    Indic coverage than any browser API. This is the working default.

`none` -- no server transcription; the browser falls back to its own engine
    and says so plainly.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import urllib.error
import urllib.request
import uuid

log = logging.getLogger("app.ai.speech")

GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
# whisper-large-v3-turbo is faster/cheaper; whisper-large-v3 is the one to use
# if you need non-English -> English translation, which turbo is not trained for.
GROQ_STT_MODEL = os.getenv("GROQ_STT_MODEL", "whisper-large-v3-turbo")
TIMEOUT = float(os.getenv("SPEECH_TIMEOUT", "45"))

# Groq accepts flac, mp3, mp4, mpeg, mpga, m4a, ogg, wav, webm.
# Browsers record webm/opus (Chrome, Firefox) or mp4/m4a (Safari).
MAX_AUDIO_BYTES = int(os.getenv("SPEECH_MAX_BYTES", str(24 * 1024 * 1024)))


class SpeechError(RuntimeError):
    """Transcription failed. Carries a message safe to show a citizen."""


def _multipart(fields: dict[str, str], filename: str, content: bytes,
               file_field: str = "file") -> tuple[bytes, str]:
    """Build a multipart/form-data body with the standard library.

    Written by hand rather than pulling in `python-multipart` or `requests`:
    this project installs with three packages and runs behind restrictive
    government proxies, and one file upload does not justify a dependency tree.
    """
    boundary = f"----boundary{uuid.uuid4().hex}"
    sep = f"--{boundary}".encode()
    parts: list[bytes] = []
    for key, value in fields.items():
        if value is None:
            continue
        parts += [sep,
                  f'Content-Disposition: form-data; name="{key}"'.encode(),
                  b"", str(value).encode("utf-8")]
    ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    parts += [sep,
              f'Content-Disposition: form-data; name="{file_field}"; '
              f'filename="{filename}"'.encode(),
              f"Content-Type: {ctype}".encode(),
              b"", content,
              f"--{boundary}--".encode(), b""]
    return b"\r\n".join(parts), f"multipart/form-data; boundary={boundary}"


class SpeechProvider:
    name = "none"

    def available(self) -> bool:
        return False

    def health(self) -> dict:
        return {"provider": self.name, "available": self.available()}

    def transcribe(self, audio: bytes, filename: str,
                   language: str | None = None) -> dict:
        raise SpeechError("No server-side transcription is configured.")


class GroqWhisper(SpeechProvider):
    """Whisper on Groq. Uses the same key as the classification engine."""

    name = "groq"

    def __init__(self, model: str | None = None):
        self.model = model or GROQ_STT_MODEL

    @property
    def api_key(self) -> str | None:
        return os.getenv("GROQ_API_KEY") or None

    def available(self) -> bool:
        return bool(self.api_key)

    def health(self) -> dict:
        return {"provider": self.name, "available": self.available(),
                "model": self.model, "base_url": GROQ_BASE_URL,
                "reason": None if self.available() else "GROQ_API_KEY is not set"}

    def transcribe(self, audio: bytes, filename: str,
                   language: str | None = None) -> dict:
        if not self.available():
            raise SpeechError("GROQ_API_KEY is not set.")
        # Passing the language improves both accuracy and latency; omitting it
        # lets Whisper detect, which is what we want when the citizen has not
        # told us what they are about to speak.
        body, ctype = _multipart(
            {"model": self.model, "response_format": "verbose_json",
             "temperature": "0", "language": language},
            filename, audio)
        req = urllib.request.Request(
            f"{GROQ_BASE_URL}/audio/transcriptions", data=body,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": ctype},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            log.warning("Groq transcription failed: HTTP %s %s", exc.code, detail)
            raise SpeechError(f"Transcription service returned HTTP {exc.code}.") from exc
        except Exception as exc:                          # noqa: BLE001
            log.warning("Groq transcription failed: %s", exc)
            raise SpeechError("Could not reach the transcription service.") from exc
        return {"text": (data.get("text") or "").strip(),
                "language": data.get("language") or language,
                "provider": self.name, "model": self.model}


class XVoiceProvider(SpeechProvider):
    """XVoice (xvoicekeyboard.com).

    UNVERIFIED. The real request/response contract has not been confirmed
    against the live service, so this implements the common convention and
    makes every part of it configurable:

        XVOICE_STT_URL    full endpoint URL            (required to enable)
        XVOICE_API_KEY    bearer token                 (optional)
        XVOICE_FILE_FIELD multipart field for audio    (default "file")
        XVOICE_LANG_FIELD field carrying the language  (default "language")
        XVOICE_TEXT_KEY   JSON key holding transcript  (default "text")
        XVOICE_MODEL      optional model identifier

    If the real API differs, this class is the only thing that changes. If
    XVoice is an on-device keyboard rather than a service, delete this class --
    the integration then belongs in the text field, not on the server.
    """

    name = "xvoice"

    def __init__(self):
        self.url = os.getenv("XVOICE_STT_URL") or None
        self.file_field = os.getenv("XVOICE_FILE_FIELD", "file")
        self.lang_field = os.getenv("XVOICE_LANG_FIELD", "language")
        self.text_key = os.getenv("XVOICE_TEXT_KEY", "text")
        self.model = os.getenv("XVOICE_MODEL") or None

    @property
    def api_key(self) -> str | None:
        return os.getenv("XVOICE_API_KEY") or None

    def available(self) -> bool:
        return bool(self.url)

    def health(self) -> dict:
        return {"provider": self.name, "available": self.available(),
                "url": self.url, "model": self.model,
                "verified": False,
                "reason": None if self.available() else "XVOICE_STT_URL is not set"}

    def transcribe(self, audio: bytes, filename: str,
                   language: str | None = None) -> dict:
        if not self.available():
            raise SpeechError("XVOICE_STT_URL is not set.")
        fields = {self.lang_field: language}
        if self.model:
            fields["model"] = self.model
        body, ctype = _multipart(fields, filename, audio, file_field=self.file_field)
        headers = {"Content-Type": ctype}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                payload = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            log.warning("XVoice transcription failed: HTTP %s %s", exc.code, detail)
            raise SpeechError(f"XVoice returned HTTP {exc.code}.") from exc
        except Exception as exc:                          # noqa: BLE001
            log.warning("XVoice transcription failed: %s", exc)
            raise SpeechError("Could not reach XVoice.") from exc
        try:
            data = json.loads(payload)
            text = data.get(self.text_key) or ""
            detected = data.get("language") or language
        except json.JSONDecodeError:
            text, detected = payload.strip(), language   # plain-text response
        return {"text": str(text).strip(), "language": detected,
                "provider": self.name, "model": self.model}


def get_speech_provider() -> SpeechProvider:
    """XVoice wins when configured; Groq Whisper is the working default."""
    forced = (os.getenv("SPEECH_PROVIDER") or "").strip().lower()
    xvoice, groq = XVoiceProvider(), GroqWhisper()
    if forced == "xvoice":
        return xvoice
    if forced == "groq":
        return groq
    if forced == "none":
        return SpeechProvider()
    if xvoice.available():
        return xvoice
    if groq.available():
        return groq
    return SpeechProvider()
