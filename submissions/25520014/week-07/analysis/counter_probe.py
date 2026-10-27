"""Raised asks: does the ladder counter once, then take any offer at or above the reserve?"""
import os, sys, threading, time
W = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, W); os.chdir(W)
import uvicorn, seller
from buyer import send, status_text
seller.DESK.ask = {"lamp": 130, "chair": 210, "bookshelf": 300}
threading.Thread(target=lambda: uvicorn.run(seller.build_app(9931), host="127.0.0.1", port=9931, log_level="warning"), daemon=True).start()
time.sleep(3)
for conv in (["I want to buy a lamp", "60", "accept"], ["I want to buy a chair", "190"], ["I want to buy a chair", "150", "185"],
             ["I want to buy a bookshelf", "140", "170", "230"], ["I want to buy a lamp", "130"]):
    tid = None
    for text in conv:
        t = send("http://127.0.0.1:9931/", text, tid); tid = t["id"]
        print(f"  {text!r} -> [{t['status']['state'][11:]}] {status_text(t)[:90]!r}")
        if t["status"]["state"] != "TASK_STATE_INPUT_REQUIRED":
            break
    print()
