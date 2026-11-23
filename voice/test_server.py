import unittest

import server


class Tests(unittest.TestCase):
    def test_lang(self):
        self.assertEqual(server.detect_lang("晚上好，报告好了"), "zh")
        self.assertEqual(server.detect_lang("Tolong kirim laporan rapat hari ini ke saya"), "id")
        self.assertEqual(server.detect_lang("Please send me today's meeting report"), "en")

    def test_clean_and_chunks(self):
        self.assertEqual(server.clean_for_speech("**Hi** [Lorium](http://x) see https://a.b/c\n- one"), "Hi Lorium see link one")
        self.assertEqual(server.clean_for_speech('All good.\n```hud\n[{"type":"stat","ti'), "All good.")
        self.assertEqual(server.clean_for_speech("Run ```ls``` then.\n```json\n{\"a\": 1"), "Run then.")
        parts = server.chunks("One. " * 200, limit=50)
        self.assertTrue(all(len(p) <= 50 for p in parts))
        self.assertEqual(server.chunks("你好。今天好吗？"), ["你好。 今天好吗？"])


    def test_google(self):
        import tempfile
        import urllib.error
        tmp = tempfile.mkdtemp()
        server._USAGE = f"{tmp}/usage.json"
        server.GOOGLE_KEY, server.GOOGLE_LIMIT = "k", 1000
        body = server.google_request("Halo", "id", "Kore", 1.1)
        self.assertEqual(body["voice"], {"languageCode": "id-ID", "name": "id-ID-Chirp3-HD-Kore"})
        self.assertEqual(body["audioConfig"]["speakingRate"], 1.1)
        self.assertNotIn("speakingRate", server.google_request("Hi", "en", rate=1.0)["audioConfig"])
        sent = []
        wav, lang, name = server.synthesize_google("Laporan hari ini sudah siap untuk dibacakan", post=lambda b: sent.append(b) or b"RIFF")
        self.assertEqual((wav, lang, name), (b"RIFF", "id", "id-ID-Chirp3-HD-Callirrhoe"))
        self.assertEqual(server.google_usage(), len("Laporan hari ini sudah siap untuk dibacakan"))

        def no_rate(b):
            if "speakingRate" in b["audioConfig"]:
                raise urllib.error.HTTPError(server.GOOGLE_URL, 400, "bad", {}, None)
            return b"RIFF"
        self.assertEqual(server.synthesize_google("Halo semua", rate=1.2, post=no_rate)[0], b"RIFF")
        # Near the monthly limit: refused, so the caller speaks with Kokoro instead.
        with self.assertRaises(RuntimeError):
            server.synthesize_google("x" * 990, post=lambda b: b"RIFF")
        server.GOOGLE_KEY = ""
        with self.assertRaises(RuntimeError):
            server.synthesize_google("Halo", post=lambda b: b"RIFF")

    def test_decode(self):
        import shutil
        import subprocess
        if not shutil.which("ffmpeg"):
            self.skipTest("no ffmpeg")
        for fmt, codec, extra in (("mp4", "aac", ["-movflags", "frag_keyframe+empty_moov"]), ("webm", "libopus", [])):  # Safari, Chrome
            wav = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:a", codec, *extra, "-f", fmt, "pipe:1"],
                                 capture_output=True, check=True).stdout
            audio = server.decode(wav)
            self.assertTrue(14000 < len(audio) < 18000, (fmt, len(audio)))
        with self.assertRaises(ValueError):
            server.decode(b"not audio")


if __name__ == "__main__":
    unittest.main()
