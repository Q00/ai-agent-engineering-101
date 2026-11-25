"""Ground truth for the challenge drivers: does a catalog item satisfy a request's spec?

Only the drivers use this. The seller never sees a spec; it gets the buyer's words.
A spec: {"category": str, "types": [substr], "groups": [material or group], "min": {attr: n}, "max": {attr: n}, "flags": {flag: bool}}
"""


def satisfies(item, spec):
    if item["category"] != spec["category"]:
        return False, f"category {item['category']} != {spec['category']}"
    if spec.get("types") and not any(t in item["type"] for t in spec["types"]):
        return False, f"type {item['type']} not in {spec['types']}"
    if spec.get("groups") and item["group"] not in spec["groups"] and item["material"] not in spec["groups"]:
        return False, f"material {item['material']} ({item['group']}) not in {spec['groups']}"
    for a, v in spec.get("min", {}).items():
        if item["attrs"].get(a) is None or item["attrs"][a] < v:
            return False, f"{a}={item['attrs'].get(a)} < {v}"
    for a, v in spec.get("max", {}).items():
        if item["attrs"].get(a) is None or item["attrs"][a] > v:
            return False, f"{a}={item['attrs'].get(a)} > {v}"
    for f, v in spec.get("flags", {}).items():
        if item["flags"].get(f) != v:
            return False, f"{f}={item['flags'].get(f)} != {v}"
    return True, "fits"
