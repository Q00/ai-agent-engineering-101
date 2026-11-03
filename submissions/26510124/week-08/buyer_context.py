"""Compress negotiation context with code; preserve exact parsed price history."""


def excerpt(text, limit):
    if len(text) <= limit:
        return text
    marker = "\n[... omitted excerpt ...]\n"
    half = (limit - len(marker)) // 2
    return text[:half] + marker + text[-half:]


def compact_dialogue(history, seller_message, read_ask):
    sellers = [h["text"] for h in history if h["role"] == "seller"]
    prior = sellers[:-1] if sellers and sellers[-1] == seller_message else sellers
    remembered = prior[:1]
    if prior and prior[-1] not in remembered:
        remembered.append(prior[-1])
    offers = [int(h["text"]) for h in history if h["role"] == "buyer"
              and h["text"].isascii() and h["text"].isdigit() and len(h["text"]) <= 18]
    return {
        "seller_message": excerpt(seller_message, 1600),
        "history": [{"role": "seller", "text": excerpt(text, 240)} for text in remembered],
        "negotiation": {
            "buyer_offers": offers[-4:],
            "seller_asks": [read_ask(text) for text in sellers[-5:]],
            "excerpts_omitted_text": len(seller_message) > 1600 or any(len(t) > 240 for t in remembered),
        },
    }
