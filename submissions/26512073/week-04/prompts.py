FORMAT_PROMPTS = {
    "free": (
        "Write your message in plain English."
    ),
    "tagged": (
        "Start your message with exactly one tag: "
        "(propose), (accept-proposal), (reject-proposal), or (refuse). "
        "After the tag, write one plain English sentence."
    ),
    "structured": (
        "Write exactly one JSON object with fields "
        "'performative' and 'content'. "
        "'performative' must be one of the four allowed acts. "
        "'content' must be an object. "
        "For propose, put the integer offer price in content.price. "
        "For other acts, use an empty content object. "
        "Do not include text outside the JSON object."
    ),
}


def make_system_prompt(role, item, limit, condition):
    if role == "buyer":
        role_prompt = (
            f"You are buying a {item}. "
            f"Your private budget is {limit}. "
            "Do not agree to pay more than your budget. "
            "Try to buy at a low price."
        )
    elif role == "seller":
        role_prompt = (
            f"You are selling a {item}. "
            f"Your private reserve is {limit}. "
            "Do not agree to sell below your reserve. "
            "Try to sell at a high price."
        )
    else:
        raise ValueError(f"Unknown role: {role}")

    common_prompt = (
        "Negotiate with the other agent. "
        "Keep your private limit secret. "
        "Use integer prices. "
        "Four acts are allowed: "
        "propose offers a price; "
        "accept-proposal accepts the other side's last proposed price "
        "and ends with a deal; "
        "reject-proposal rejects an offer but continues the negotiation; "
        "refuse ends the negotiation without a deal. "
        "You may accept only if the other side has proposed a price. "
        "The buyer speaks first, and you alternate. "
        "The negotiation ends after at most eight messages."
    )

    return (
        role_prompt + "\n\n"
        + common_prompt + "\n\n"
        + FORMAT_PROMPTS[condition]
    )


if __name__ == "__main__":
    for condition in FORMAT_PROMPTS:
        print(f"\n--- Buyer: {condition} ---")
        print(make_system_prompt("buyer", "desk lamp", 45, condition))

    print("\n--- Seller: structured ---")
    print(make_system_prompt("seller", "desk lamp", 30, "structured"))