"""harness.rules_watch: first copy kept quietly, a change alerts and flags the knowledge resting on the market."""
import os
import tempfile
import unittest
from pathlib import Path

from harness import alert, knowledge, rules_watch


class RulesWatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = {k: os.environ.get(k) for k in ("HARNESS_ROOT", "HARNESS_OUT", "TELEGRAM_BOT_TOKEN",
                                                    "TELEGRAM_CHAT_ID")}
        os.environ.update(HARNESS_ROOT=self.tmp.name, HARNESS_OUT=str(Path(self.tmp.name) / "out"),
                          TELEGRAM_BOT_TOKEN="", TELEGRAM_CHAT_ID="")

    def tearDown(self):
        for k, v in self.old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        self.tmp.cleanup()

    def test_change(self):
        pages = {"rules": "Patch 3.3\nshop ALL = 0.5 book", "agent": "# agent"}
        knowledge.write({"id": "k", "about": "score", "claim": "book is half", "source": "market:rules",
                         "method": "read"}, now=1)
        self.assertFalse(rules_watch.check(now=10, fetcher=pages.get)["rules"]["changed"])
        self.assertFalse(rules_watch.check(now=20, fetcher=pages.get)["rules"]["changed"])
        self.assertEqual(alert.recent(now=20), [])
        pages["rules"] = "Patch 3.4\nshop ALL = 0.6 book"
        out = rules_watch.check(now=30, fetcher=pages.get)
        self.assertTrue(out["rules"]["changed"])
        self.assertEqual(out["rules"]["patch"], "3.4")
        self.assertFalse(out["agent"]["changed"])
        a = alert.recent(now=30)[-1]
        self.assertEqual(a["level"], "now")
        self.assertIn("3.3 → 3.4", a["text"])
        self.assertIn("review", knowledge.get(about="score", now=40)[0])

    def test_html_text(self):
        t = rules_watch.text_of("<html><style>x{}</style><h1>Rules</h1><p>Patch notes 3.3</p><script>v=1</script>",
                                True)
        self.assertEqual(t, "Rules\nPatch notes 3.3")
        self.assertEqual(rules_watch.patch_of("Patch 3.2 ... Patch 3.10 ... patch 3.3"), "3.10")


if __name__ == "__main__":
    unittest.main()
