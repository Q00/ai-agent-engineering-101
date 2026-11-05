"""Launch the unchanged kit with receipt protection and bounded buyer reports."""
import os
from pathlib import Path

from receipt_leases import ReceiptLeaseStore
from run_round3 import verify_kit
from buyer_forum import drain_buyer_forum
from market_clock import install_market_clock

ROOT = Path(__file__).resolve().parent


def install_buyer_forum(kit):
    """Use the kit's existing sync cadence; no new thread or model call."""
    original = kit.outbox
    if getattr(original, "_buyer_forum_installed", False) is True:
        return

    def outbox(market, md):
        try:
            return original(market, md)
        finally:
            try:
                result = drain_buyer_forum(market, md)
                if result["status"] in {"sent", "rate_limited", "uncertain", "rejected"}:
                    kit.log("[buyer-forum] send=" + result["status"])
            except Exception as exc:
                # Forum outages must not stop inventory sync or trading. The
                # spool decides whether retrying an HTTP attempt is safe.
                kit.log("[buyer-forum] send_error=" + type(exc).__name__)

    outbox._buyer_forum_installed = True
    kit.outbox = outbox


def main():
    verify_kit(ROOT)
    # Import executable kit code only after the recorded upstream hash passes.
    from guarded_checkout import GuardedAcpShop
    from vendor import market_kit_v4 as kit
    ReceiptLeaseStore(ROOT / ".market").initialize()
    kit.AcpShop = GuardedAcpShop
    install_market_clock(kit, ROOT / ".market")
    install_buyer_forum(kit)
    # Only this guarded launcher opts the child seller into expiring receipts.
    # Running the plain pinned kit keeps the previous conservative hold policy.
    os.environ["MARKET_RECEIPT_GUARD"] = "1"
    return kit.main()


if __name__ == "__main__":
    main()
