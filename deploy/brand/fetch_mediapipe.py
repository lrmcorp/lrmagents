"""Fetch MediaPipe hand tracking for the Vault's gesture mode, at image build time.

Pinned and checksummed: the npm package @mediapipe/tasks-vision (Apache-2.0) and
Google's hand landmarker model. Only the files the page loads are kept, so the
browser never fetches code or models from anywhere but this server.
"""
import hashlib
import io
import pathlib
import sys
import tarfile
import urllib.request

VERSION = "1.1.1"
PACKAGE = (f"https://registry.npmjs.org/@mediapipe/tasks-vision/-/tasks-vision-{VERSION}.tgz",
           "fbbea455a8b50c31c39092027b3548336de198bfcdbaf75cc08245e773011b8f")
MODEL = ("https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task",
         "fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1")
KEEP = {"package/vision_bundle.js": "vision_bundle.js",
        "package/wasm/vision_wasm_internal.js": "wasm/vision_wasm_internal.js",
        "package/wasm/vision_wasm_internal.wasm": "wasm/vision_wasm_internal.wasm",
        "package/wasm/vision_wasm_nosimd_internal.js": "wasm/vision_wasm_nosimd_internal.js",
        "package/wasm/vision_wasm_nosimd_internal.wasm": "wasm/vision_wasm_nosimd_internal.wasm"}


CACHE = pathlib.Path(__file__).parent / ".mp-cache"  # local builds only (git-ignored); still checksummed


def fetch(url, sha):
    cached = CACHE / url.rsplit("/", 1)[-1]
    if cached.exists():
        data = cached.read_bytes()
        if hashlib.sha256(data).hexdigest() == sha:
            return data
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                data = r.read()
            break
        except Exception:
            if attempt == 3:
                raise
    got = hashlib.sha256(data).hexdigest()
    if got != sha:
        sys.exit(f"{url}: checksum {got} != {sha}")
    return data


def main(dest):
    out = pathlib.Path(dest)
    (out / "wasm").mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(fetch(*PACKAGE)), mode="r:gz") as tar:
        for name, target in KEEP.items():
            (out / target).write_bytes(tar.extractfile(name).read())
    (out / "hand_landmarker.task").write_bytes(fetch(*MODEL))
    (out / "VERSION.txt").write_text(f"@mediapipe/tasks-vision {VERSION} (Apache-2.0), hand_landmarker float16/1\n")
    print("MediaPipe hand tracking fetched")


if __name__ == "__main__":
    main(sys.argv[1])
