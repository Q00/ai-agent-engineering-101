"""Shop forum posts written by code from our stock and the public ledger. No model, and no sentence from anyone else's post.

Every figure comes from a record: inventory and selling terms for what we offer now, public rows and the board for what we did,
bad numbers included. Asks and public terms are the only prices shown. safe() is the last check before a post is queued.
"""

from datetime import datetime, timedelta, timezone
import re
import statistics

from low_star_monitor import clean
from purchase_monitor import settlement_price

KST = timezone(timedelta(hours=9))
NAME_LIMIT = 40
UNITS_PER_NOTICE = 4
MIN_RECORD_DEALS = 20
MAX_REFS = 10
COMPARE_HOURS, COMPARE_MIN_SAMPLES = 6, 3
FORBIDDEN = re.compile(r"ignore\s+(all|previous|prior)|disregard|previous instruction|system prompt|you must|must (choose|buy|pick)|이전 지시|지시를 무시|시스템 프롬프트|선택하라|고르라|반드시 (구매|선택)"
                       r"|원가|하한|공급 전략|역제안|\bcost\b|\bfloor\b|\bmargin\b"
                       r"|보장|guarantee|최저가|최고의|추천|recommend|cheapest|\bbest\b|<[^>]*>|\{|\}", re.I)


def safe(text):
    return not FORBIDDEN.search(str(text))


def checked_at(now):
    return datetime.fromtimestamp(now, KST).strftime("%m-%d %H:%M KST")


def unit_line(unit, terms, now):
    """What we offer for one unit: never the reserve, only the ask and the grade on its certificate."""
    line = f"{clean(unit.get('name') or unit['sku'], NAME_LIMIT)} (SKU {unit['sku']}), 인증 {unit['grade']}등급, 호가 {terms['quote']} credits, 기본 보증 0시간"
    if unit.get("kind") == "food" and unit.get("expires_at"):
        line += f", 유통기한 약 {max(0, int((unit['expires_at'] - now) // 3600))}시간 남음"
    return line


def stock_notice(lines, category, now):
    leaf = clean(str(category).split(">")[-1], 40)
    return (f"kcwarni 재고 안내 [{leaf}] {checked_at(now)} 확인: " + "; ".join(lines)
            + ". 확인 시각 이후 팔렸거나 조건이 바뀌었을 수 있습니다. 기본 보증 외 시간은 체결 영수증에 적힙니다. 결제 전 영수증과 시장 인증서로 확인해 주세요.")


def reply(line, now):
    return (f"kcwarni {checked_at(now)} 확인 재고: {line}. 확인 시각 이후 팔렸거나 조건이 바뀌었을 수 있습니다. 결제 전 영수증과 시장 인증서로 확인해 주세요.")


def own_rows(rows, own_units):
    """Our sales and the breaks and refunds on them, from public rows."""
    settles = [r for r in rows if r.get("kind") == "settle" and (r.get("data") or {}).get("unit") in own_units]
    urls = {r["data"].get("shop") for r in settles}
    broken = [r for r in rows if r.get("kind") == "broken" and (r.get("data") or {}).get("shop") in urls]
    refunds = [r for r in rows if r.get("kind") == "refund" and (r.get("data") or {}).get("shop") in urls]
    return settles, broken, refunds


def honest_record(rows, own_units, board, now):
    """(text, refs), or None with too small a sample. Breaks go into the refs first, so they cannot be left out."""
    settles, broken, refunds = own_rows(rows, own_units)
    if len(settles) < MIN_RECORD_DEALS or not board or board.get("deals") is None:
        return None
    honest = sum(r["data"].get("claim_honest") is True for r in settles)
    covered = sum(bool(r["data"].get("warranty_hours")) for r in settles)
    start, end = min(r["ts"] for r in settles), max(r["ts"] for r in settles)
    refunded = sum(r["data"].get("refunded") or 0 for r in broken + refunds)
    text = (f"kcwarni 공개 원장 기록 {checked_at(start)} ~ {checked_at(end)}: 저장된 표본의 정산 {len(settles)}건 중 claim_honest {honest}건, 고장 {len(broken)}건, 환불액 {refunded} credits, "
            f"보증 있는 거래 {covered}건(나머지는 보증 0시간이라 고장 시 환불 없음). 공식 게시판 전체: 거래 {board.get('deals')}, 고장 {board.get('broken')}, 환불 {board.get('refunds')}, "
            f"별점 {board.get('stars')}({board.get('reviews')}건), 등급 표시 {board.get('claims')}건 중 정직 {board.get('honest_claims')}건. 표본은 전수가 아니며 refs는 그중 일부입니다.")
    newest_first = sorted(broken + refunds, key=lambda r: -r["seq"]) + sorted(settles, key=lambda r: -r["seq"])
    return text, [r["seq"] for r in newest_first[:MAX_REFS]]


def public_median(rows, unit, now):
    """Median public price of the same SKU and grade with no warranty in the last COMPARE_HOURS. (median, samples) or None."""
    prices = []
    for row in rows:
        data = row.get("data") or {}
        if (row.get("kind") == "settle" and data.get("item") == unit["sku"] and data.get("grade") == unit["grade"] and not data.get("warranty_hours")
                and 0 <= now - row.get("ts", 0) <= COMPARE_HOURS * 3600):
            amount = settlement_price(data)
            if amount is not None:
                prices.append(amount)
    return (statistics.median(prices), len(prices)) if len(prices) >= COMPARE_MIN_SAMPLES else None


def market_compare(unit, terms, median, samples, now):
    return (f"kcwarni 호가와 공개 시세 {checked_at(now)}: {clean(unit.get('name') or unit['sku'], NAME_LIMIT)} (SKU {unit['sku']}), 인증 {unit['grade']}등급, 호가 {terms['quote']} credits, 기본 보증 0시간. "
            f"같은 SKU·등급·보증 0시간의 최근 {COMPARE_HOURS}시간 공개 정산 {samples}건 중앙값 {median:g} credits. 저장된 표본 기준이며 전수가 아니고, 공개 정산가는 협상 후 가격입니다.")
