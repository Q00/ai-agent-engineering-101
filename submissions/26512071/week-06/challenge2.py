"""Challenge 2 run: start the 20-shop directory, run shopper.py on a shopping list, stop.

    python challenge2.py 2>&1 | tee logs/challenge-2/run.txt
"""
import subprocess
import sys
from pathlib import Path

import shops

HERE = Path(__file__).parent
BUDGET = 1100
ITEMS = ["a wooden bookcase for about 40 books", "an office chair to work at a desk all day",
         "a floor lamp to read by", "a cabinet for shoes by the front door"]

if __name__ == "__main__":
    logdir = HERE / "logs" / "challenge-2"
    logdir.mkdir(parents=True, exist_ok=True)
    procs, urls = shops.start(20, logdir)
    try:
        code = subprocess.run([sys.executable, "shopper.py", "--budget", str(BUDGET), *ITEMS], cwd=HERE).returncode
    finally:
        for p in procs:
            p.terminate()
    sys.exit(code)
