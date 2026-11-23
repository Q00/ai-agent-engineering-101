"""Bare-handed difficulty screen: which candidate work items does a model fail?

A tool only buys something where the model would otherwise be wrong, so the
task set has to be calibrated before the arms are run, not after. Each
candidate is answered with no tools at all and checked by `verify`; the useful
band is a bare-handed pass rate somewhere between a third and two thirds, since
that is where holding the matching tool changes the outcome.

    uv run difficulty.py 2>&1 | tee logs/difficulty-screen.txt

Provider comes from the environment, the same way `backend` picks it, so the
same candidates can be screened against more than one model.
"""
import concurrent.futures as cf
import pathlib
import sys

sys.path[:0] = [str(pathlib.Path(__file__).resolve().parent),
                str(pathlib.Path(__file__).resolve().parent.parent)]

import backend                                   # noqa: E402
import verify                                    # noqa: E402

SAMPLES = 3
SYSTEM = ("You are a contractor. Do the work and reply with the answer only, "
          "no preamble. A number means the number; Python means the code; "
          "sentences mean those sentences.")
SOURCE = ('"This study empirically validates the efficiency of '
          'negotiation-based task allocation in multi-agent systems."')

CANDIDATES = [
    ("calc 2-term", "Compute 48317 * 7629 - 91824 * 3067 and return only the "
     "number.", {"type": "exact", "expect": str(48317 * 7629 - 91824 * 3067)}),
    ("calc 3-term", "Compute 48317 * 7629 - 91824 * 3067 + 25518 * 4409 and "
     "return only the number.",
     {"type": "exact", "expect": str(48317 * 7629 - 91824 * 3067 + 25518 * 4409)}),
    ("calc 4-term", "Compute 73841 * 2957 + 18234 * 6611 - 40922 * 1873 + 90145 "
     "and return only the number.",
     {"type": "exact",
      "expect": str(73841 * 2957 + 18234 * 6611 - 40922 * 1873 + 90145)}),
    ("write 30 words", f"Rewrite this for a 10-year-old in exactly 3 sentences "
     f"using exactly 30 words in total: {SOURCE}",
     {"type": "rule", "sentences": 3, "total_words": 30}),
    ("write 24 words", f"Rewrite this for a 10-year-old in exactly 4 sentences "
     f"using exactly 24 words in total, no sentence longer than 8 words: "
     f"{SOURCE}",
     {"type": "rule", "sentences": 4, "total_words": 24, "max_words": 8}),
    ("code median", "Write a Python function median(xs) returning the median of "
     "a list of numbers. Even-length lists average the two middle values.",
     {"type": "pytest", "asserts": ["median([3, 1, 2]) == 2",
                                    "median([4, 1, 3, 2]) == 2.5",
                                    "median([5]) == 5"]}),
    ("code top_k", "Write a Python function top_k(counts, k) taking a dict of "
     "item to count. Return the k items with the highest counts as a list, ties "
     "broken alphabetically by item, and the whole sorted list if k exceeds the "
     "number of items.",
     {"type": "pytest", "asserts": ["top_k({'a': 3, 'b': 1, 'c': 3}, 2) == ['a', 'c']",
                                    "top_k({'b': 2, 'a': 2}, 1) == ['a']",
                                    "top_k({'x': 1}, 5) == ['x']",
                                    "top_k({}, 3) == []"]}),
    ("code parse_range", "Write a Python function parse_range(s) turning a "
     "string like '1-3,5,7-9' into a sorted list of unique integers. Single "
     "numbers and ranges may be mixed, ranges are inclusive, a descending range "
     "contributes nothing, and an empty string gives an empty list.",
     {"type": "pytest", "asserts": ["parse_range('1-3,5,7-9') == [1, 2, 3, 5, 7, 8, 9]",
                                    "parse_range('') == []",
                                    "parse_range('4') == [4]",
                                    "parse_range('3-1') == []",
                                    "parse_range('2,2,1') == [1, 2]"]}),
]


def main():
    meter = backend.Meter()

    def once(job):
        name, text, spec = job
        return name, verify.check(spec, backend.ask(SYSTEM, text, meter))

    jobs = [c for c in CANDIDATES for _ in range(SAMPLES)]
    with cf.ThreadPoolExecutor(max_workers=6) as pool:
        out = list(pool.map(once, jobs))

    print(f"model={backend.MODEL}  samples={SAMPLES}  no tools\n")
    for name, _, _ in CANDIDATES:
        hits = [ok for got, ok in out if got == name]
        band = ("in band" if 0 < sum(hits) < len(hits)
                else "too easy" if all(hits) else "too hard")
        print(f"  {name:18s} {sum(hits)}/{len(hits)}   {band}")
    print(f"\ncalls={meter.calls} failures={meter.failures}")


if __name__ == "__main__":
    main()
