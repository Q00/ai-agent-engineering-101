"""ops/ci.py and ops/deploy_pull.py: the pieces that decide whether a pushed commit reaches the live market."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ops"))
import ci
import deploy_pull as d

R = d.REL


def test_only_service_code_needs_a_restart():
    assert not d.needs_restart([R + "tests/test_x.py", R + "logs/a.txt", R + "README.md"])
    assert d.needs_restart([R + "buyer_r3.py"]) and d.needs_restart([R + "r3lib/config.py"])


def test_benign_otel_errors_are_not_counted():
    log = ("07:21:54 [seller] ValueError: <Token var=<ContextVar name='current_context' default={} at 0x79>\n"
           "07:21:55 [seller] Traceback (most recent call last):\n"
           "07:21:56 [buyer] KeyError: 'price'\n"
           "07:22:00 [kit] your seller exited (code 1), starting it again\n"
           "07:22:01 [kit] watchdog: SystemExit: [market] join as shop failed\n"
           "07:22:02 [market] ledger #1: paid\n")
    got = d.errors(log)
    assert len(got) == 3 and "KeyError" in got[0] and "seller exited" in got[1] and "watchdog" in got[2]


def test_scope_and_secret_paths():
    assert ci.check_scope(["submissions/26510358/week-08/a.py"])[0]
    assert not ci.check_scope(["README.md"])[0]
    assert ci.SECRET_PATH.search("submissions/26510358/week-08/.market/api-key.env")
    assert ci.SECRET_PATH.search("x/shop_key.pem") and not ci.SECRET_PATH.search("x/shop_r3.py")


def test_secret_text():
    assert ci.SECRET_TEXT.search("-----BEGIN " + "PRIVATE KEY-----")    # split: the CI secret scan reads this file too
    assert ci.SECRET_TEXT.search("token = 'sbp_" + "a" * 40 + "'")
    assert not ci.SECRET_TEXT.search("MIN_SURPLUS_RATIO = 0.30")
