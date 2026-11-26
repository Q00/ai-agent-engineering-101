import concurrent.futures
import json
import pathlib
import subprocess

HERE = pathlib.Path(__file__).parent
TARGETS = {
    "11im": "8a317b4dfc83910cda5305e4b4ea97f07e8e0815",
    "Lee-sungsoo": "36bd2a589541ff4cdd5cac567f0ac9397f68929f",
    "dragonash22": "ca10429c357c06965a1b3a8e4d986a284cba9ce0",
    "JangSeongWoong": "68484fd9e8a77846fdee393cd6f8cc1b7cc96dc8",
    "Q00": "9d349639623d6ede65022275ad2bdd48aa8fbabc",
}


def inspect(target):
    owner, sha = target
    result = subprocess.run(["gh", "api", f"repos/{owner}/ai-agent-engineering-101/git/trees/{sha}?recursive=1"], capture_output=True, check=False)
    (HERE / f"github-tree-{owner}.json").write_bytes(result.stdout)
    if result.returncode:
        return {"owner": owner, "error": result.stderr.decode()}
    tree = json.loads(result.stdout)["tree"]
    paths = [entry["path"] for entry in tree if entry["type"] == "blob" and (
        ("week-07" in entry["path"] or "week-06" in entry["path"]) and entry["path"].endswith((".py", ".ts", ".js", ".md", "agent.json", "wrangler.toml"))
        or (owner == "Q00" and any(word in entry["path"] for word in ("market", "worker")) and entry["path"].endswith((".py", ".ts", ".js")))
    )]
    return {"owner": owner, "sha": sha, "paths": paths}


with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
    results = list(pool.map(inspect, TARGETS.items()))
(HERE / "github-paths.json").write_text(json.dumps(results, indent=2))
for result in results:
    print(json.dumps(result))
