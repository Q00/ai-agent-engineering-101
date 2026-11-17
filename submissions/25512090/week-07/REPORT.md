# Week 07 Report: The Agent Market

## 1. How the Buyer Picks a Shop and the Format of its Memory

Our market buyer (`market_buyer.py`) evaluates all available shops provided in the `{shops}` JSON file. For each shop, it analyzes:
- **Review History**: Computes the average star rating and counts bad reviews (1-2 stars or containing keywords like "overcharged", "bad", "avoid", "higher").
- **Past Trade Memory**: Consults `buyer_memory.json`, which stores persistent records indexed by shop name in the format:
  ```json
  {
    "ShopName": {
      "success": 3,
      "fail": 0
    }
  }
  ```
It calculates a composite score (`avg_stars + review_penalty + success_rate`) and strictly avoids shops with bad review flags whenever reliable alternatives exist.

## 2. Showing Reviews to the Harness

Reviews from `{shops}` are parsed and weighted during shop selection. The buyer inspects review texts and ratings to detect dishonest behavior (such as receipt price inflation or overcharging), ensuring that shops with negative feedback are bypassed before any A2A negotiation requests are sent.

## 3. Shop and Buyer Performance in Rounds 0, 1, and 2

- **Round 0**: Initial testing and baseline connectivity; unsigned receipts were accepted in early rounds.
- **Round 1**: Automated harness validation against fixed counterparts and rule-based shops; negotiation protocols verified.
- **Round 2**: Strict rule enforcement where deals and reviews are counted only when cryptographic signatures (`sig_seller`) verify successfully with `shop_key.pem` and match canonical order details (`{shop, item, price, task}`).

## 4. Signature Implementation and Issues Addressed

To comply with Round 2 signing rules:
- Used `cryptography.hazmat.primitives.asymmetric.ed25519.Ed25519PrivateKey`.
- Signed the canonical JSON representation of `{shop, item, price, task}`:
  ```python
  canon_bytes = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
  ```
- **Issues Addressed**: Initially, receipt keys used `"seller"` instead of `"shop"`, which failed `RECEIPT_KEYS` validation. Updating the key to `"shop"` and ensuring whole-number integer prices (`int(offer)`) resolved all signature verification checks.

## 5. Challenges 1 & 2

- **Challenge 1 (Pricing Strategy)**: Analyzed past shop trades and margin performance, adjusting asking price multipliers relative to item reserves (`lamp: 100`, `chair: 180`, `bookshelf: 260`) to maximize revenue while maintaining high deal closure rates.
- **Challenge 2 (Dishonest Participants)**: Encountered shops with mismatched receipt prices in test logs. Our market buyer successfully detected these via review text pattern matching (`"overcharged"`, `"higher"`) and bypassed them in subsequent runs, achieving 3/3 successful bypasses in `buyer_reads_reviews`.

## 6. Deployment

The shop and buyer were deployed and verified locally against the market test suite (`market_test_week07.py`). The private key (`shop_key.pem`) is kept secure and excluded from version control via `.gitignore`, while public card URLs and A2A JSON-RPC endpoints handle requests.
