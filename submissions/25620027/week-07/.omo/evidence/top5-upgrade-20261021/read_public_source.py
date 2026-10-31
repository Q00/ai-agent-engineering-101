import concurrent.futures
import json
import pathlib
import urllib.request

HERE = pathlib.Path(__file__).parent
paths = json.loads((HERE / "github-paths.json").read_text())
targets = []
for record in paths:
    for path in record["paths"]:
        if path.startswith("submissions/") and pathlib.Path(path).name in (
            "index.ts", "seller.py", "pricing_policy.py", "model_harness.py", "harness.py", "market_buyer.py", "REPORT.md", "README.md", "agent.json"
        ):
            targets.append((record["owner"], record["sha"], path))


def read(target):
    owner, sha, path = target
    url = f"https://raw.githubusercontent.com/{owner}/ai-agent-engineering-101/{sha}/{path}"
    with urllib.request.urlopen(url, timeout=25) as response:
        content = response.read(1_000_000)
    destination = HERE / "public-source" / owner / path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    return {"url": url, "permalink": f"https://github.com/{owner}/ai-agent-engineering-101/blob/{sha}/{path}", "bytes": len(content)}


with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
    results = list(pool.map(read, targets))
(HERE / "source-manifest.json").write_text(json.dumps(results, indent=2))
print(json.dumps(results, indent=2))
