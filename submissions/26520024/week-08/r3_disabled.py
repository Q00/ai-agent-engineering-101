"""Do not silently fall back to the incompatible Round 0-2 buyer."""
if __name__ == '__main__':
    raise SystemExit('This agent requires Round 3 orders with acceptable SKUs and a market_buyer hook.')
