"""Unmodified warranty/receipt functions captured from seller.py at 3de0399.
Offline compatibility oracle; do not update it with the experiment implementation.
"""
import re
import seller
import hard_limits
import shop_memory
WARRANTY_HOURS = re.compile(r"(\d+(?:\.\d+)?)\s*(?:h\b|hours?|시간)", re.I)
log = seller.log
def warranty_terms(self, deal, text):
        """Goods: a warranty of hard_limits.SELLER_GOODS_WARRANTY_HOURS (buyers' kits refuse to pay without one, the kit's
        example asks for 6 h), or longer if asked. The break chance is already in the reserve (profit_price floor), and
        goods that break too often were refused at the first message. Returns the sentence for the buyer, or None (food,
        already set, not wanted)."""
        if deal.get("kind") != "goods" or "warranty" in deal \
                or re.search(r"\b(no|without) (a )?(warranty|guarantee)|보증\s*(없이|필요\s*없)", text or "", re.I):
            return None
        cfg = self.warranty_cfg()
        asked = [float(h) for h in WARRANTY_HOURS.findall(text or "")]
        hours = max(hard_limits.SELLER_GOODS_WARRANTY_HOURS, min(max(asked), cfg["max_hours"]) if asked else 0)
        hours = int(hours) if float(hours).is_integer() else hours
        deal["warranty"] = hours
        log(f"WARRANTY {hours} h for {deal['id']}: break chance {deal.get('break', cfg['prior_break']):.1%} is in the reserve {deal['reserve']}")
        return f"This unit comes with a {hours} h warranty: if it breaks within {hours} h, you get the full price back."

def stock_receipt(self, task_id, deal, price):
        """Round 3: the receipt names the SKU (or the order's item when the buyer asked by category) and carries the terms
        the checkout ships under: the unit fixes what ships either way."""
        receipt = {"shop": self.name, "item": deal.get("receipt_item") or deal["id"], "price": price, "task": task_id,
                   "unit": deal["unit"]}
        grade = self.stock.grade(deal["unit"])
        if grade in ("A", "B", "C"):
            receipt["claim"] = grade
        hours = deal["warranty"] if "warranty" in deal else shop_memory.load_pricing().get("warranty_hours", 0)
        if deal.get("kind") == "goods" and hours:
            receipt["warranty_hours"] = hours
        return receipt
