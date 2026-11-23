"""A category with no block of its own still gets a value line when it is asked for (live 11-12: the shop operator
asked for one such category every wake for hours, saw only ranges, and held its work as "output cut")."""

import json

from kernel import policy_edit as pe
from tests.test_experiments import CATS, root  # noqa: F401  (root is a fixture)


def _bare(root):
    pol = json.load(open(pe.path(root)))
    return sorted(set(CATS) - set(pol.get("categories") or {}))


def test_a_category_without_a_block_shows_what_it_inherits(root):
    cat = _bare(root)[0]
    pol = json.load(open(pe.path(root)))
    line = [ln for ln in pe.view(root, CATS, show=[cat]).split("\n") if ln.startswith(f"- {cat}: ")]
    assert len(line) == 1 and "직접 적힌 키 없음" in line[0]
    vals = json.loads(line[0].split(": ", 1)[1].rsplit(" (", 1)[0])
    assert vals == {k: pe.effective(pol, cat, k) for k in pe._keys(cat)} and any(v is not None for v in vals.values())


def test_without_show_the_view_is_unchanged_and_lists_every_bare_category(root):
    view = pe.view(root, CATS)
    bare = _bare(root)
    assert not any(ln.startswith(f"- {b}: ") for b in bare for ln in view.split("\n"))
    assert all(b in view for b in bare)
