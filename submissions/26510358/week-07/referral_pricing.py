"""A disclosed review referral code, not proof that a buyer read a review."""
import re


def code_claim(text, metadata, rule):
    if not rule or not rule.get("enabled"):
        return None
    code = rule["code"]
    if isinstance(metadata, dict) and metadata.get("referralCode") == code:
        return "message.metadata.referralCode"
    if re.search(r"(?<![A-Za-z0-9_])" + re.escape(code) + r"(?![A-Za-z0-9_])", text, re.I):
        return "message.text"
    return None


def item_query(text, rule):
    """Remove only the configured code when performing an exact catalog lookup."""
    return re.sub(r"(?<![A-Za-z0-9_])" + re.escape(rule["code"]) + r"(?![A-Za-z0-9_])",
                  "", text, flags=re.I).strip()


def referral_terms(item, reference_price, quote_policy, claim_source):
    """Calculate a final whole-credit quote once; never relax the item reserve."""
    rule = quote_policy.get("review_referral") or {}
    terms = {"price": reference_price, "applied": False, "claimed": bool(claim_source),
             "claim_source": claim_source, "read_verified": False}
    if not rule.get("enabled"):
        terms["reason"] = "disabled"
        return terms
    terms["code"] = rule["code"]
    configured = quote_policy["phase"] in rule["phases"] and item["id"] in rule["item_ids"]
    terms["fixed_quote"] = configured
    if not claim_source:
        terms["reason"] = "code_not_present"
    elif not configured:
        terms["reason"] = "not_available_for_phase_or_item"
    else:
        percent = rule["percent"]
        if isinstance(percent, bool) or not isinstance(percent, int) or not 0 < percent < 100:
            raise ValueError("Referral percentage must be an integer between 1 and 99")
        total = reference_price * (100 - percent)
        if total % 100 or total // 100 < item["reserve"]:
            # Never round a partial saving and advertise it as the full percentage.
            terms["reason"] = "full_discount_unavailable_within_whole_credit_floor"
        else:
            terms.update(price=total // 100, applied=True, reason="code_promotion",
                         reference_price=reference_price, percent=percent,
                         saving=reference_price - total // 100)
    return terms


def review_notice(rule):
    """Clearly separate our own advertisement from a review of another shop."""
    if not rule or not rule.get("enabled"):
        return ""
    return (" Ad (my shop): Ssadassa Shop / myeonggi, code " + rule["code"] +
            " for " + str(rule["percent"]) + "% off new recovery-mode lamp/chair/bookshelf quotes; "
            "include item + code in the first message. Acquisition prices excluded.")


def append_review_notice(review, notice, *, limit=400):
    """Reserve room for the entire disclosed ad in every public review result."""
    if not notice:
        return review
    footer = notice.strip()
    body = review.rstrip()
    if body.endswith(footer):
        body = body[:-len(footer)].rstrip()
    room = max(0, limit - len(footer) - 1)
    if len(body) > room:
        body = body[:room - 3].rstrip() + "..." if room >= 3 else body[:room]
    return (body + " " + footer).lstrip()
