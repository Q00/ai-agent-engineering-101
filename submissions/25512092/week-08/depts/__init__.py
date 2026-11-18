"""The shop as a company. Each module is one department; the old top-level names are aliases (see each shim).

  ceo.py          shop and buyer case (strategy.py); ceo_policy.py writes policy.json
  purchasing.py   restock plan inside the purchasing policy and the hard limits (restock.py runs it)
  sales.py        floors, clearance/aging markdowns, endgame, first ask, warranty (pricing.py)
  logistics.py    stock at cost, inventory, perishable bought/sold/wasted
  cs.py           forum queues and posts (forum_post.py); the negotiation itself is seller_r3.py
  qc.py           sales reports, money summary, buying split history (sales_report.py)
  experiments.py  variant weights used by sales and the buyer
  buyer_biz.py    the buyer business (market_buyer.py), the CEO's buyer section only
Hard limits: limits.py. Policy: policy.py / policy.json. Decisions: decisions.py. Reports: dept_reports.py.
"""
