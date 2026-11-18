"""Code haggling for every product as a 30% experiment (person's decision 11/07 ~02:20). Runs once, detached:

  11/07 11:00  held (urgent Telegram, nothing changed) when the 6 h volume-pricing guard reverted the price rules or
               did not pass, when bundle 2 C was held at 10:00 (restock_c.json not enabled), or when it is inside the
               no-restart window 15:30-20:00; otherwise code_haggle.json {"enabled", "share": 0.3, "since"} and a
               seller-only restart (the new seller code reads the flag per negotiation) -> Telegram
  results      alerts.haggle_report: 24 h after "since", per arm (code / harness, sofas left out): negotiations,
               success rate, profit per negotiation, harness calls per negotiation -> Telegram
Log: logs/haggle_rollout.log
"""
import json
import os
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
os.chdir(HERE)
import alerts  # noqa: E402
import decisions  # noqa: E402
from seller_conc_rollout import restart_seller, seller_back  # noqa: E402

LOG = os.path.join(HERE, "logs", "haggle_rollout.log")
AT = datetime(2026, 11, 7, 11, 0).timestamp()
NO_RESTART = (datetime(2026, 11, 7, 15, 30).timestamp(), datetime(2026, 11, 7, 20, 0).timestamp())
R = "person's decision 11/07 ~02:20"


RS = ("person's decision 11/07 ~10:00: a named SKU I do not hold -> 'X is not in stock' + up to 3 same-category "
      "candidates (keyword + price +-30%, price +-30%, rest) with SKU, name, price; a receipt only after the buyer "
      "names one; category orders unchanged; offers parsed without SKU tokens")


def subst_on():
    """The 11/07 seller changes that go on with this restart: substitute offers, and (person's decision ~09:45) the
    category receipt item + the grade claim experiment on 50% of non-food negotiations."""
    now = round(time.time())
    with open(os.path.join(HERE, "substitute.json"), "w", encoding="utf-8") as f:
        json.dump({"enabled": True, "since": now}, f)
    with open(os.path.join(HERE, "receipt_rules.json"), "w", encoding="utf-8") as f:
        json.dump({"category_item": True, "claim_share": 0.5, "since": now}, f)
    decisions.record("sales", "receipt_rules_on", "receipt_rules", {"category_item": True, "claim_share": 0.5},
                     "person's decision 11/07 ~09:45: category orders get the category name as receipt item (unit in "
                     "the unit field); non-food negotiations: 50% put the real grade in the receipt claim; on with "
                     "this seller restart")


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m-%d %H:%M:%S} {m}\n")


def main():
    log("haggle rollout waiting for 11:00")
    while time.time() < AT:
        time.sleep(min(60, AT - time.time()))
    from depts import volume
    from depts.purchasing import c_rules
    v = volume.rules()
    why = None
    if v.get("reverted_at") or not v.get("enabled"):
        why = "07:47 안전장치 발동 (박리다매 가격 규칙 원복됨)"
    elif v.get("check_bad") is not False:
        why = "07:47 안전장치 점검이 통과로 기록되지 않음"
    elif not c_rules().get("enabled"):
        why = "10:00 2차 적용(C)이 보류됨"
    elif NO_RESTART[0] <= time.time() < NO_RESTART[1]:
        why = "15:30~20:00 재시작 금지 시간"
    if why:
        decisions.record("sales", "code_haggle_held", "code_haggle", None, f"{R}: held at 11:00: {why}")
        if NO_RESTART[0] <= time.time() < NO_RESTART[1]:
            alerts.send("urgent", "haggle_held", f"전 품목 코드 흥정 30% 실험 11:00 적용 보류: {why}. 대체 영수증 수정도 "
                        "재시작 금지 시간이라 보류. 아무것도 바꾸지 않음", log)
            log(f"held: {why}")
            return
        subst_on()                                  # 11/07 ~10:00: the substitute fix goes in anyway, before 15:30
        t = time.time()
        killed = restart_seller()
        log(f"held: {why}; substitute fix on, seller restarted (killed {killed})")
        decisions.record("sales", "substitute_on", "substitute", None, f"{RS}; code haggle held ({why}), seller "
                         "restarted for this fix only")
        time.sleep(120)
        back = seller_back(t)
        alerts.send("urgent", "haggle_held", f"전 품목 코드 흥정 30% 실험 11:00 적용 보류: {why}. 대체 영수증 수정만 "
                    f"적용, seller 재시작 " + (f"정상 ({back})" if back else "후 확인 안 됨, 점검 필요")
                    + ". 6시간 뒤 대체 제안 보고", log)
        return
    now = round(time.time())
    with open(os.path.join(HERE, "code_haggle.json"), "w", encoding="utf-8") as f:
        json.dump({"enabled": True, "share": 0.3, "since": now}, f)
    subst_on()
    t = time.time()
    killed = restart_seller()
    log(f"flag on, substitute fix on, seller restarted (killed {killed})")
    decisions.record("sales", "substitute_on", "substitute", None, f"{RS}; same restart as the code haggle experiment")
    decisions.record("sales", "code_haggle_on", "code_haggle", 0.3,
                     f"{R}: 30% of negotiations (sofas always code) haggle by code: never above the first ask, 3 steps "
                     "to the floor, accept an offer >= the next counter; shop_haggle records the arm and harness calls; "
                     "seller restarted 11:00")
    time.sleep(120)
    back = seller_back(t)
    alerts.send("urgent" if not back else "info", "haggle_on",
                f"전 품목 코드 흥정 30% 실험 + 대체 영수증 수정 적용 {datetime.fromtimestamp(t):%H:%M}: seller 재시작 "
                + (f"정상 ({back})" if back else "후 확인 안 됨, 점검 필요") + ". 대체 제안 6시간, 흥정 24시간 뒤 보고", log)
    log("done")


if __name__ == "__main__":
    main()
