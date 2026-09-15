# Voice input

## Status, stated plainly

**XVoice (xvoicekeyboard.com) is not yet integrated.** There is an adapter slot
for it (`XVoiceProvider` in `app/ai/speech.py`), wired end to end and tested
against a stub, but its request and response contract has **not been verified
against the live service**. What is implemented is the common convention, made
entirely configurable so that matching the real API is a change of environment
variables or, at worst, one class.

Earlier versions of this repo described XVoice as "the production intake layer."
That was wrong and has been removed — nothing was ever wired to it.

## Why voice was unreliable before

The first implementation used the browser's `SpeechRecognition` API and nothing
else. For this project specifically that was a poor choice:

| Problem | Consequence |
|---|---|
| Chrome/Safari only | Firefox users get a dead microphone button. |
| **Requires a secure origin** | Open the app from a phone at `http://192.168.x.x:8000` and the browser blocks the mic outright. **This is the most common reason voice "doesn't work"** — it fails exactly when you demo on real hardware. |
| Chrome ships audio to Google | Needs internet, and supports Google's language list. |
| Weak Indic/African coverage | Worst precisely where this platform needs it most. |
| Defaulted to `en-IN` when no language was picked | Speaking Tamil produced confident English gibberish rather than an error. |

The last one is fixed outright: the browser path now refuses to start until a
language is chosen, because that engine cannot detect one.

## How it works now

The **server** decides which path a browser takes, via `GET /api/voice/status`.

### Path 1 — record and upload (preferred)

`MediaRecorder` captures audio in the browser, which POSTs it to
`/api/voice/transcribe`; the server transcribes it. This works in **every**
browser, over plain HTTP on a LAN, in whatever languages the configured model
supports.

```
browser mic ──▶ MediaRecorder ──▶ POST /api/voice/transcribe ──▶ provider ──▶ text
                (webm/mp4)         (base64 JSON)                 xvoice|groq
```

### Path 2 — browser recognition (fallback)

Used only when no server transcription is configured. The UI now says so, and
when it cannot work it names the specific reason — insecure origin, unsupported
browser, denied permission, unsupported language — rather than failing silently.

## Configuration

| Variable | Purpose |
|---|---|
| `XVOICE_STT_URL` | Endpoint URL. **Setting this enables XVoice and takes priority over Groq.** |
| `XVOICE_API_KEY` | Bearer token, if required. |
| `XVOICE_FILE_FIELD` | Multipart field name for the audio (default `file`). |
| `XVOICE_LANG_FIELD` | Field carrying the language hint (default `language`). |
| `XVOICE_TEXT_KEY` | JSON key holding the transcript (default `text`). |
| `XVOICE_MODEL` | Optional model identifier. |
| `GROQ_API_KEY` | Enables Whisper on Groq — the working default. |
| `GROQ_STT_MODEL` | Default `whisper-large-v3-turbo`. Use `whisper-large-v3` if you need non-English → English translation, which turbo is not trained for. |
| `SPEECH_PROVIDER` | Force `xvoice`, `groq`, or `none`. |
| `SPEECH_MAX_BYTES` | Upload ceiling (default 24MB). |

Selection order: `SPEECH_PROVIDER` if set → XVoice if `XVOICE_STT_URL` is set →
Groq if `GROQ_API_KEY` is set → none.

### Working today, with the key you already have

```bash
export GROQ_API_KEY=gsk_...
./run.sh
```

Whisper large v3 covers ~99 languages with far better Indic accuracy than any
browser engine, and reuses the same key as the classification engine.

## Integrating the real XVoice

Answering these three questions is all that stands between the slot and a real
integration:

1. **Is XVoice an HTTP service, or an on-device Android keyboard (IME)?**
   This changes everything:
   - *HTTP service* → fill in the `XVOICE_*` variables above; likely done.
   - *Android keyboard* → **there is nothing to integrate on the server at
     all.** A keyboard works at the OS level: the citizen installs XVoice, taps
     the mic on their own keyboard, and it types into any text field — including
     this app's. The correct work then lives in the text input (IME-safe
     handling, `lang`/`inputmode` attributes), not in `speech.py`, and
     `XVoiceProvider` should simply be deleted.
2. **If it is a service:** endpoint URL, auth scheme, the multipart field name
   for audio, accepted audio formats, and the JSON shape of the response.
3. **Which languages does it support, and how is the language specified** — a
   parameter, or auto-detected?

## Endpoints

- `GET /api/voice/status` — which path this browser should take, and why.
- `POST /api/voice/transcribe` — `{audio_base64, filename, language?}` →
  `{text, language, provider, model, bytes}`.

Audio is base64 in JSON rather than a multipart upload so the project keeps its
three-package dependency list; clips are seconds long, so the encoding overhead
is irrelevant. The **outbound** multipart request to the STT service is built by
hand with the standard library (`_multipart` in `app/ai/speech.py`, covered by
`test_multipart_body_is_well_formed`).
