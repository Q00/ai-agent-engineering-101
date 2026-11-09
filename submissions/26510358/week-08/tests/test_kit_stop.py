"""2026-11-09: every tunnel rotation left the old cloudflared running (14 of them under the kit): stop() only
signalled a process group, and cloudflared is not a group leader, so killpg raised and the fallback did too."""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import market_kit


def test_stop_ends_a_child_that_is_not_a_group_leader():
    proc = subprocess.Popen(["sleep", "60"])            # opened like open_tunnel opens cloudflared
    market_kit.stop(proc)
    assert proc.poll() is not None
