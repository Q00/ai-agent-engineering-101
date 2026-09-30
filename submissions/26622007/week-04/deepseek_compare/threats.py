"""What happened after a DeepSeek holder threatened with the shotgun?

threat_labels.csv is a hand label of every armed episode whose tool action or own message used the gun
to pressure the other side (aiming, firing, "so I suggest you accept"). Tool used as a message channel,
system-prompt echoes and "I'm putting the shotgun away" were read and left out. This script adds, from the
logs, the other side's next message and the episode outcome, and compares the holder's surplus share in
threat episodes with the unarmed DeepSeek control.
"""
import csv
import json
from statistics import mean

import compare_models as cm

ds = cm.ds


def main():
    scenarios = {str(s["id"]): s for s in json.loads((ds.ROOT / "scenarios.json").read_text())}
    labels = list(csv.DictReader((ds.HERE / "threat_labels.csv").open()))
    rows = {}
    for design in ds.DESIGNS:
        for r in csv.DictReader((ds.HERE / "runs" / ds.suite_name(design) / "results.csv").open()):
            rows[(r["run"], r["scenario"])] = r
    out = []
    for lab in labels:
        row = rows[(lab["run"], lab["scenario"])]
        holder = row["armed_role"]
        events = cm.events_of(lab["run"], lab["scenario"])
        reply = next((e["text"] for e in events if e["event"] == "message" and e["speaker"] != holder
                      and e["turn"] > int(lab["turn"])), "")
        share = ""
        sc = scenarios[lab["scenario"]]
        if row["outcome"] == "deal" and sc["reserve"] < sc["budget"]:
            p, w = int(row["price"]), sc["budget"] - sc["reserve"]
            share = round((sc["budget"] - p) / w if holder == "buyer" else (p - sc["reserve"]) / w, 3)
        out.append({**lab, "holder": holder, "deal_possible": row["deal_possible"], "outcome": row["outcome"],
                    "price": row["price"], "correct": row["correct"], "violation": row["violation"],
                    "turns": row["turns"], "holder_share": share, "other_next_message": reply})
    cm.luna_compare.write_csv(ds.HERE / "runs" / "threats.csv", out)
    armed_eps = sum(1 for r in rows.values() if r["armed_role"] != "none")
    print(f"threat episodes: {len(out)} of {armed_eps} armed DeepSeek episodes")
    for design in ds.DESIGNS:
        n = sum(o["design"] == design for o in out)
        if n:
            print(f"  {design}: {n}")
    feasible = [o for o in out if o["deal_possible"] == "1"]
    print(f"feasible threat episodes: {len(feasible)}, deals {sum(o['outcome'] == 'deal' for o in feasible)}, "
          f"correct {sum(o['correct'] == '1' for o in feasible)}, violations {sum(o['violation'] == '1' for o in feasible)}")
    shares = [o["holder_share"] for o in out if o["holder_share"] != "" and o["holder"] == "buyer"]
    print(f"buyer-holder share in threat deals: {mean(shares):.3f} (n={len(shares)})")
    for o in out:
        print(f"- {o['design']:17} {o['run'].split('-')[-3]}-{o['run'].split('-')[-2]}-{o['run'][-2:]} s{o['scenario']} "
              f"{o['outcome']:7} {o['price']:>4} share={o['holder_share']!s:5} | {o['evidence'][:60]} || {o['other_next_message'][:150]}")


if __name__ == "__main__":
    main()
