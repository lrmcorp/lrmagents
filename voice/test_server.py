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


if __name__ == "__main__":
    unittest.main()
