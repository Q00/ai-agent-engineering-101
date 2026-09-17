"""Does the permission table actually produce a capability difference?

Every contractor is asked to do every skill's work, so each column is the same
work done by someone who holds a different tool. Any difference across a row is
differentiation the tools caused; a row that is uniform is a skill where the
tool bought nothing, and that is worth knowing before spending a full run on it.

    uv run probe.py 2>&1 | tee logs/probe-tools.txt
"""
import concurrent.futures as cf
import json
import pathlib
import sys

sys.path[:0] = [str(pathlib.Path(__file__).resolve().parent),
                str(pathlib.Path(__file__).resolve().parent.parent)]

import backend                                   # noqa: E402
import orchestrator as orc                       # noqa: E402
import verify                                    # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent


def main():
    tasks = json.loads((HERE / "tasks_ext.json").read_text(encoding="utf-8"))
    team = orc.build_team()
    work = [(t["elements"][0]["need"], t["desc"], t["elements"][0]["verify"])
            for t in tasks[:3]]
    meter = backend.Meter()
    trace = []

    def cell(member, item):
        need, text, spec = item
        answer, used, denied = orc.do_work(member, text, meter, trace.append)
        return dict(name=member["name"], need=need, tools=used, refused=denied,
                    passed=verify.check(spec, answer),
                    answer=answer.strip()[:70].replace("\n", " | "))

    jobs = [(m, w) for w in work for m in team]
    with cf.ThreadPoolExecutor(max_workers=5) as pool:
        out = list(pool.map(lambda pair: cell(*pair), jobs))

    for need, _, _ in work:
        row = sorted((r for r in out if r["need"] == need),
                     key=lambda r: r["name"])
        print(f"{need:6s} " + "  ".join(
            f"{r['name']}:{'PASS' if r['passed'] else 'FAIL'}"
            f"/{r['tools']}tool/{r['refused']}ref" for r in row))
    print()
    for r in sorted(out, key=lambda r: (r["need"], r["name"])):
        print(f"  {r['need']:6s} {r['name']}  "
              f"{'PASS' if r['passed'] else 'FAIL'}  {r['answer']}")
    print(f"\ncalls={meter.calls} tokens={meter.tokens} "
          f"failures={meter.failures}")
    for line in trace:
        if "[tool]" in line:
            print(line)


if __name__ == "__main__":
    main()
