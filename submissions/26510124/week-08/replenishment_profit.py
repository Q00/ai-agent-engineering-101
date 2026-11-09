"""Bounded replenishment recovery after observed refunds, not a demand forecast."""

def refund_recovery(metric, cfg):
    rules = cfg.get("refund_recovery", {})
    if rules.get("enabled") is not True or metric.get("unknown", 0):
        return False
    mature, refunded = metric.get("mature", 0), metric.get("refunded", 0)
    revenue = metric.get("mature_revenue", 0)
    return (type(mature) is int and mature >= rules["min_mature_sales"]
            and type(refunded) is int and 0 < refunded * 100 <= mature * rules["max_refund_percent"]
            and revenue > 0 and metric.get("speed_score", 0) > 0
            and metric.get("net_estimate", 0) * 100 >= revenue * rules["min_net_margin_percent"])
