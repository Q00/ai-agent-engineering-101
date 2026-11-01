"""Record the kit's settled receipt. Neutral factual reviews, no LLM call."""
import argparse
import json
from pathlib import Path

from feedback import append_event
from inventory import read_json

ROOT = Path(__file__).resolve().parent


def review(deal):
    settled, receipt = deal["settled"], deal["receipt"]
    if not isinstance(settled.get("ledger"), int) or not isinstance(settled.get("sku"), str):
        raise ValueError("Expected the kit's Round 3 settlement receipt")
    honest = settled.get("claim_honest")
    text = (f"Settled {settled['sku']} at {receipt['price']}; received grade {settled.get('grade')}. "
            f"Warranty {settled.get('warranty_hours') or 0} h. Ledger {settled['ledger']}.")
    if honest is False:
        text += " The recorded grade claim did not match."
    return {"stars": 1 if honest is False else 3, "review": text}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--deal", type=Path, required=True)
    args = parser.parse_args()
    deal = read_json(args.deal)
    result = review(deal)
    # Do not copy private order budget/mandates into review text or shared evidence.
    append_event(ROOT / "logs" / "settled-deals.jsonl", {
        "order": deal["order"]["order"], "receipt": deal["receipt"], "settled": deal["settled"]})
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
