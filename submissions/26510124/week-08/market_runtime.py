"""Launch the unchanged classroom kit with the submission's receipt guard."""
import os
from pathlib import Path

from receipt_leases import ReceiptLeaseStore
from run_round3 import verify_kit

ROOT = Path(__file__).resolve().parent


def main():
    verify_kit(ROOT)
    # Import executable kit code only after the recorded upstream hash passes.
    from guarded_checkout import GuardedAcpShop
    from vendor import market_kit_v4 as kit
    ReceiptLeaseStore(ROOT / ".market").initialize()
    kit.AcpShop = GuardedAcpShop
    # Only this guarded launcher opts the child seller into expiring receipts.
    # Running the plain pinned kit keeps the previous conservative hold policy.
    os.environ["MARKET_RECEIPT_GUARD"] = "1"
    return kit.main()


if __name__ == "__main__":
    main()
