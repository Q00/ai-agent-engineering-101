"""Runner for Challenge 1: Large catalog & natural language search negotiation."""
import subprocess
import time
import httpx
import os
from pathlib import Path

def main():
    sub = Path(__file__).resolve().parent
    log_dir = sub / "logs" / "challenge-1"
    log_dir.mkdir(parents=True, exist_ok=True)
    
    port = 9995
    url = f"http://127.0.0.1:{port}"
    
    # Start seller with catalog config
    seller_cmd = ["python", "seller.py", "--config", "catalog.json", "--port", str(port)]
    log_file = open(log_dir / "challenge.log", "w")
    
    log_file.write("=== Starting Challenge 1 Seller ===\n")
    log_file.flush()
    
    seller_proc = subprocess.Popen(seller_cmd, cwd=sub, stdout=log_file, stderr=subprocess.STDOUT)
    
    # Wait for agent card
    time.sleep(2)
    
    try:
        r = httpx.get(f"{url}/.well-known/agent-card.json", timeout=5)
        log_file.write(f"Agent Card status: {r.status_code}\n")
        log_file.write(f"Agent Card: {r.text}\n")
    except Exception as e:
        log_file.write(f"Failed to reach agent card: {e}\n")
        
    # Run buyer with NL description
    buyer_cmd = ["python", "buyer.py", url, "something wooden to keep about 40 books, not taller than my desk", "300"]
    log_file.write(f"=== Running Buyer with NL query ===\n")
    log_file.flush()
    
    res = subprocess.run(buyer_cmd, cwd=sub, capture_output=True, text=True)
    log_file.write(f"Buyer stdout:\n{res.stdout}\n")
    log_file.write(f"Buyer stderr:\n{res.stderr}\n")
    log_file.write(f"Buyer exit code: {res.returncode}\n")
    
    log_file.close()
    seller_proc.terminate()
    seller_proc.wait(timeout=5)
    print("Challenge 1 completed. Log saved to logs/challenge-1/challenge.log")

if __name__ == "__main__":
    main()
