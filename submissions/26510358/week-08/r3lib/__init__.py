"""Round 3 agent library: memory (SQLite over the public ledger and our own records), market access,
deterministic pricing and restock math, a bounded LLM call, and a small A2A client.

Design rule: numbers (prices, quantities, accept policies) come from code and memory; the LLM only reads or
writes natural language (shop replies it cannot parse, forum posts) and never sets money.
"""
