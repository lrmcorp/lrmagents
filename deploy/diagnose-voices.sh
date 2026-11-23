#!/usr/bin/env bash
# Run on the VPS by .github/workflows/diagnose.yml (part "voices"): speaks one sentence with
# several Google Chirp 3 HD female voices through the voice service, so the owner can pick one.
# Prints each clip as base64 lines; the workflow encrypts the output before it is logged.
cd ~/cowagent || exit 1
sudo -n docker compose -f deploy/compose.yaml exec -T cowagent python - <<'PY' 2>&1
import base64, json, os, time, urllib.request
token = os.environ.get("EXTERNAL_API_TOKEN", "")
health = json.loads(urllib.request.urlopen("http://voice:8095/health", timeout=10).read())
print("health:", {k: health.get(k) for k in ("google", "google_voice", "google_chars", "google_limit")})
text = ("Oke, quick update ya. Hari ini tiga cabang aman, tapi PIK perlu di-follow up: ada dua customer "
        "yang belum dibalas dari pagi. Summary-nya udah aku siapin, mau aku bacain sekarang atau habis meeting?")
for voice in ("Sulafat", "Pulcherrima", "Callirrhoe", "Laomedeia"):
    body = json.dumps({"text": text, "lang": "id", "engine": "google", "gvoice": voice, "format": "ogg"}).encode()
    req = urllib.request.Request("http://voice:8095/tts", data=body, method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            audio, name = r.read(), r.headers.get("X-Voice")
    except Exception as e:
        print(f"VOICE {voice} FAILED {e} {getattr(e, 'read', lambda: b'')()[:200]!r}")
        continue
    print(f"VOICE {voice} {name} {len(audio)} bytes in {time.time() - started:.1f}s")
    data = base64.b64encode(audio).decode()
    for i in range(0, len(data), 300):
        print("B64 " + voice + " " + data[i:i + 300])
PY
