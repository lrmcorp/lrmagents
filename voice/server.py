"""LRMagent voice service (internal, Docker network only).

POST /stt   body: audio (any format ffmpeg/PyAV reads: webm, ogg, m4a, wav)
            -> {"text", "language", "duration"}          (faster-whisper, multilingual)
POST /tts   {"text", "lang"?: "en"|"zh"|"id", "voice"?, "speed"?, "format"?: "wav"|"ogg"}
            -> audio                                        (Kokoro 82M, int8)
GET  /health, /voices
Every call but /health needs "Authorization: Bearer <COWAGENT_API_TOKEN>".

Languages: English and Mandarin use Kokoro's native voices; Bahasa Indonesia is spoken
with Indonesian phonemes (espeak-ng) on a Kokoro voice, as Kokoro has no Indonesian speaker.
"""
import hashlib
import hmac
import io
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODELS = os.environ.get("MODELS", "/models")
TOKEN = os.environ.get("COWAGENT_API_TOKEN", "")
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")
KOKORO = {
    "kokoro-v1.0.int8.onnx": ("https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/kokoro-v1.0.int8.onnx",
                              "ae315a79b623f244700e4afb9246c46a26066782e049ba174bf3ba433970ee9c"),
    "voices-v1.0.bin": ("https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/voices-v1.0.bin",
                        "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d"),
}
# Default voice per language; any Kokoro voice name can be asked for instead.
VOICES = {"en": ("af_heart", "en-us"), "zh": ("zf_xiaoxiao", "cmn"), "id": ("af_heart", "id")}
LANG_CODE = {"en": "en-us", "en-gb": "en-gb", "zh": "cmn", "id": "id", "ms": "id"}
MAX_TEXT = 4000

_lock = threading.Lock()
_kokoro = None
_whisper = None
_stt_lock = threading.Lock()
_tts_lock = threading.Lock()


def _fetch(name):
    path = os.path.join(MODELS, name)
    url, sha = KOKORO[name]
    if os.path.exists(path):
        return path
    tmp = path + ".part"
    with urllib.request.urlopen(url, timeout=600) as r, open(tmp, "wb") as f:
        digest = hashlib.sha256()
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            digest.update(chunk)
            f.write(chunk)
    if digest.hexdigest() != sha:
        os.remove(tmp)
        raise RuntimeError(f"{name}: checksum mismatch")
    os.replace(tmp, path)
    return path


def kokoro():
    global _kokoro
    with _lock:
        if _kokoro is None:
            from kokoro_onnx import Kokoro
            _kokoro = Kokoro(_fetch("kokoro-v1.0.int8.onnx"), _fetch("voices-v1.0.bin"))
        return _kokoro


def whisper():
    global _whisper
    with _lock:
        if _whisper is None:
            from faster_whisper import WhisperModel
            _whisper = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8", download_root=os.path.join(MODELS, "whisper"),
                                    cpu_threads=int(os.environ.get("OMP_NUM_THREADS", "2")))
        return _whisper


# ---- language --------------------------------------------------------------------------
_ID_WORDS = set("""yang dan di ini itu aku saya kamu anda tidak apa sudah akan bisa untuk dengan dari ke juga ada
mau tolong terima kasih bagaimana kenapa kapan dimana siapa berapa hari ini besok kemarin sekarang nanti tapi atau
karena jadi lagi sama banget gak nggak enggak dong ya kok sih deh nih buat bikin lihat cari kirim laporan rapat""".split())


def detect_lang(text):
    if re.search(r"[一-鿿]", text):
        return "zh"
    words = re.findall(r"[a-zA-Z]+", text.lower())
    if not words:
        return "en"
    hits = sum(1 for w in words if w in _ID_WORDS)
    return "id" if hits / len(words) >= 0.15 or hits >= 3 else "en"


def clean_for_speech(text):
    """Markdown and links are read badly: keep the words."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+", " link ", text)
    text = re.sub(r"[*_`#>|~]+", " ", text)
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.M)
    return re.sub(r"\s+", " ", text).strip()


def chunks(text, limit=320):
    """Sentences, packed up to `limit` characters, so long answers synthesise piece by piece."""
    parts = re.split(r"(?<=[.!?。！？])\s+|(?<=[。！？])", text)
    out, cur = [], ""
    for p in (x.strip() for x in parts):
        if not p:
            continue
        if len(cur) + len(p) + 1 > limit and cur:
            out.append(cur)
            cur = p
        else:
            cur = f"{cur} {p}".strip()
    if cur:
        out.append(cur)
    return out


def synthesize(text, lang=None, voice=None, speed=1.0):
    import numpy as np
    text = clean_for_speech(text)[:MAX_TEXT]
    if not text:
        raise ValueError("nothing to say")
    lang = lang if lang in VOICES or lang in LANG_CODE else detect_lang(text)
    default_voice, code = VOICES.get(lang, VOICES["en"])
    code = LANG_CODE.get(lang, code)
    voice = voice or default_voice
    k = kokoro()
    if voice not in k.get_voices():
        voice = default_voice
    pieces, rate = [], 24000
    with _tts_lock:
        for part in chunks(text):
            samples, rate = k.create(part, voice=voice, speed=max(0.6, min(1.6, float(speed or 1.0))), lang=code)
            pieces.append(samples)
            pieces.append(np.zeros(int(rate * 0.12), dtype=samples.dtype))
    return np.concatenate(pieces), rate, lang, voice


def encode(samples, rate, fmt):
    import soundfile as sf
    buf = io.BytesIO()
    sf.write(buf, samples, rate, format="WAV", subtype="PCM_16")
    wav = buf.getvalue()
    if fmt != "ogg":
        return wav, "audio/wav"
    proc = subprocess.run(["ffmpeg", "-v", "error", "-f", "wav", "-i", "pipe:0", "-c:a", "libopus", "-b:a", "32k", "-ac", "1", "-f", "ogg", "pipe:1"],
                          input=wav, capture_output=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode()[:200])
    return proc.stdout, "audio/ogg; codecs=opus"


def decode(data):
    """Any recording (webm/opus from Chrome, mp4/aac from Safari, ogg, wav) -> 16 kHz mono float32.

    ffmpeg does the decoding: faster-whisper's own decoder (PyAV) changes its API between
    releases, and a browser recording should never fail on that."""
    import numpy as np
    with tempfile.NamedTemporaryFile(suffix=".audio") as f:
        f.write(data)
        f.flush()
        proc = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", f.name, "-f", "s16le", "-ac", "1", "-ar", "16000", "pipe:1"],
                              capture_output=True, timeout=120)
    if proc.returncode != 0 or not proc.stdout:
        raise ValueError("could not read that recording: " + proc.stderr.decode(errors="replace")[:160].strip())
    return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def transcribe(data, lang=None):
    audio = decode(data)
    with _stt_lock:
        # Fast settings for short spoken requests: one beam, no timestamps, no carrying text
        # between segments, silence skipped.
        segments, info = whisper().transcribe(audio, language=lang or None, vad_filter=True, beam_size=1, best_of=1,
                                              without_timestamps=True, condition_on_previous_text=False,
                                              initial_prompt="LRMagent, Lorium, Reyner, Liando Reyfort Management.")
        text = " ".join(s.text.strip() for s in segments).strip()
    language = {"ms": "id", "jw": "id"}.get(info.language, info.language)
    return {"text": text, "language": language, "duration": round(info.duration, 2)}


# ---- http ------------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "lrm-voice"

    def log_message(self, fmt, *args):  # quiet: no request lines with text in them
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorised(self):
        given = self.headers.get("Authorization", "").replace("Bearer ", "", 1).strip()
        return bool(TOKEN) and hmac.compare_digest(given.encode(), TOKEN.encode())

    def _body(self, limit):
        size = int(self.headers.get("Content-Length") or 0)
        if size > limit:
            raise ValueError("too large")
        return self.rfile.read(size)

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"ok": True, "tts_loaded": _kokoro is not None, "stt_loaded": _whisper is not None, "whisper": WHISPER_MODEL})
        if not self._authorised():
            return self._send(403, {"error": "internal endpoint"})
        if self.path == "/voices":
            return self._send(200, {"defaults": {k: v[0] for k, v in VOICES.items()}, "voices": sorted(kokoro().get_voices())})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._authorised():
            return self._send(403, {"error": "internal endpoint"})
        started = time.time()
        try:
            if self.path.startswith("/stt"):
                lang = self.headers.get("X-Lang") or None
                result = transcribe(self._body(25 << 20), lang)
                result["seconds"] = round(time.time() - started, 2)
                return self._send(200, result)
            if self.path == "/tts":
                req = json.loads(self._body(64 << 10) or b"{}")
                samples, rate, lang, voice = synthesize(str(req.get("text") or ""), req.get("lang"), req.get("voice"), req.get("speed") or 1.0)
                data, ctype = encode(samples, rate, req.get("format") or "wav")
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("X-Lang", lang)
                self.send_header("X-Voice", voice)
                self.end_headers()
                self.wfile.write(data)
                return None
            return self._send(404, {"error": "not found"})
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:  # noqa: BLE001 - the caller gets a reason, the service keeps running
            return self._send(500, {"error": f"{type(e).__name__}: {e}"[:300]})


def warm():
    # Load both models in the background so the first question is not slow.
    for load in (kokoro, whisper):
        try:
            load()
        except Exception as e:  # noqa: BLE001
            print(f"[voice] {load.__name__} not loaded yet: {e}", flush=True)


if __name__ == "__main__":
    threading.Thread(target=warm, daemon=True).start()
    port = int(os.environ.get("PORT", "8095"))
    print(f"[voice] listening on {port}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
