import unittest

import server


class Tests(unittest.TestCase):
    def test_lang(self):
        self.assertEqual(server.detect_lang("晚上好，报告好了"), "zh")
        self.assertEqual(server.detect_lang("Tolong kirim laporan rapat hari ini ke saya"), "id")
        self.assertEqual(server.detect_lang("Please send me today's meeting report"), "en")

    def test_clean_and_chunks(self):
        self.assertEqual(server.clean_for_speech("**Hi** [Lorium](http://x) see https://a.b/c\n- one"), "Hi Lorium see link one")
        parts = server.chunks("One. " * 200, limit=50)
        self.assertTrue(all(len(p) <= 50 for p in parts))
        self.assertEqual(server.chunks("你好。今天好吗？"), ["你好。 今天好吗？"])


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
