def choose_winner(bids):
    """bids contains (contractor_name, validated_bid) pairs."""
    willing = [
        (name, bid)
        for name, bid in bids
        if bid["bid"]
    ]

    if not willing:
        return None

    # Highest confidence wins.
    # On a tie, max keeps the first entry: preserve A, B, C order.
    name, bid = max(willing, key=lambda item: item[1]["confidence"])
    return name


if __name__ == "__main__":
    example = [
        ("A", {"bid": True, "confidence": 80}),
        ("B", {"bid": False, "confidence": 99}),
        ("C", {"bid": True, "confidence": 95}),
    ]
    assert choose_winner(example) == "C"

    tied = [
        ("A", {"bid": True, "confidence": 95}),
        ("C", {"bid": True, "confidence": 95}),
    ]
    assert choose_winner(tied) == "A"
    assert choose_winner([]) is None

    print("All 3 manager checks passed.")