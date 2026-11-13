"""The schemas of everything on the bus, as JSON Schema, and a small validator.

The schemas are plain JSON Schema dicts, so the MCP server and function calls can
publish the same definitions (HARNESS.md 3.1). jsonschema is not installed here, so
validate() implements only the keywords these schemas use: type, required,
properties, additionalProperties, items, enum, minimum, maximum, minLength, maxLength.
"""

SCHEMA_VERSION = 1

_TYPES = {
    "object": dict, "array": list, "string": str, "boolean": bool, "null": type(None),
}


def _is_type(value, name):
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, _TYPES[name])


def validate(obj, schema, path="$"):
    """A list of error strings; empty when obj fits schema."""
    errors = []
    types = schema.get("type")
    if types is not None:
        types = [types] if isinstance(types, str) else types
        if not any(_is_type(obj, t) for t in types):
            return [f"{path}: expected {'/'.join(types)}, got {type(obj).__name__}"]
    if "enum" in schema and obj not in schema["enum"]:
        errors.append(f"{path}: {obj!r} is not one of {schema['enum']}")
    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        if "minimum" in schema and obj < schema["minimum"]:
            errors.append(f"{path}: {obj} is below {schema['minimum']}")
        if "maximum" in schema and obj > schema["maximum"]:
            errors.append(f"{path}: {obj} is above {schema['maximum']}")
    if isinstance(obj, str) and "minLength" in schema and len(obj) < schema["minLength"]:
        errors.append(f"{path}: shorter than {schema['minLength']}")
    if isinstance(obj, str) and "maxLength" in schema and len(obj) > schema["maxLength"]:
        errors.append(f"{path}: longer than {schema['maxLength']}")
    if isinstance(obj, dict):
        props = schema.get("properties", {})
        for k in schema.get("required", []):
            if k not in obj:
                errors.append(f"{path}: missing {k!r}")
        for k, v in obj.items():
            if k in props:
                errors += validate(v, props[k], f"{path}.{k}")
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: unexpected key {k!r}")
    if isinstance(obj, list) and "items" in schema:
        for i, v in enumerate(obj):
            errors += validate(v, schema["items"], f"{path}[{i}]")
    return errors


_STR = {"type": "string", "minLength": 1}
_TS = {"type": "number", "minimum": 0}           # unix seconds
_VERSION = {"type": "integer", "minimum": 1}

# Join keys on every raw event (HARNESS.md 2): what lets slices, profiles and ledgers line up.
KEYS = {
    "type": "object",
    "properties": {k: {"type": ["string", "integer", "null"]}
                   for k in ("task", "order", "unit", "sku", "ledger_seq", "counterpart")},
    "additionalProperties": False,
}

EVENT = {
    "type": "object",
    "required": ["schema_version", "event_id", "ts", "writer", "source", "kind",
                 "strategy_version", "epoch", "card_version", "keys", "data"],
    "properties": {
        "schema_version": _VERSION,
        "event_id": _STR,
        "ts": _TS,
        "writer": _STR,
        "source": _STR,
        "kind": _STR,
        "strategy_version": {"type": ["integer", "null"]},
        "epoch": {"type": ["string", "null"]},
        "card_version": {"type": ["integer", "null"]},
        "keys": KEYS,
        "data": {"type": "object"},
    },
    "additionalProperties": False,
}

STATUS = {
    "type": "object",
    "required": ["schema_version", "runtime", "ts", "pid"],
    "properties": {
        "schema_version": _VERSION,
        "runtime": {"type": "string", "enum": ["inner", "outer"]},
        "ts": _TS,
        "pid": {"type": "integer"},
        "kit_version": {"type": ["string", "null"]},
        "round": {"type": ["integer", "null"]},
        "strategy_version": {"type": ["integer", "null"]},   # the strategy the runtime is using now
        "last_hour": {"type": "object"},                     # counts: deals, failures, rate_limited, ...
        "note": {"type": "string"},
    },
}

ALERT = {
    "type": "object",
    "required": ["schema_version", "event_id", "ts", "writer", "level", "kind", "message"],
    "properties": {
        "schema_version": _VERSION,
        "event_id": _STR,
        "ts": _TS,
        "writer": _STR,
        "level": {"type": "string", "enum": ["info", "warn", "urgent"]},
        "kind": _STR,
        "message": _STR,
        "data": {"type": "object"},
    },
}

# memory/strategy/active.json (HARNESS.md 5): priorities and hypotheses, not rules.
LIMITS = {
    "type": "object",
    "required": ["cash_floor", "max_units_per_sku", "max_stock_share", "food_share_max", "below_cost"],
    "properties": {
        "cash_floor": {"type": "integer", "minimum": 0},
        "max_units_per_sku": {"type": "integer", "minimum": 1},
        "max_stock_share": {"type": "number", "minimum": 0, "maximum": 2},   # of the starting capital (11/4: above 1 = cash turns, benchmark)
        "stock_base": {"type": "string", "enum": ["start", "equity"]},   # 11/4 14:0x: what the share is of
        "food_share_max": {"type": "number", "minimum": 0, "maximum": 1},    # of the stock value
        "below_cost": {
            "type": "object",
            "required": ["allowed", "max_loss_share_of_cost"],
            "properties": {
                "allowed": {"type": "boolean"},
                "only_in_phase": {"type": ["string", "null"]},
                "max_loss_share_of_cost": {"type": "number", "minimum": 0, "maximum": 1},
                # Welcome Food (user 11/3): below cost only on a buyer's first deal, only these kinds, only up to
                # this list price. "First deal" = not a known customer by the fingerprint guess (HARNESS 2.1)
                "first_deal_only": {"type": "boolean"},
                "kinds": {"type": "array", "items": {"type": "string", "enum": ["food", "goods"]}},
                "max_list_price": {"type": "integer", "minimum": 0},
                "grades": {"type": "array", "items": {"type": "string", "enum": ["A", "B", "C"]}},   # grade now
            },
        },
    },
}

STRATEGY = {
    "type": "object",
    "required": ["schema_version", "version", "epoch", "approved", "phase", "priorities", "limits",
                 "experiment", "delegation"],
    "properties": {
        "schema_version": _VERSION,
        "version": _VERSION,
        "epoch": _STR,
        "approved": _STR,
        "phase": _STR,
        "priorities": {"type": "array", "items": _STR},
        "context": {"type": "array", "items": _STR},   # optional: what the analysts and strategy saw, for executors
        "warranty": {                                    # optional: a warranty the shop always gives (a promise: tools add it)
            "type": "object", "required": ["hours"],
            "properties": {"hours": {"type": "number", "minimum": 0, "maximum": 168},
                           "kinds": {"type": "array", "items": {"type": "string", "enum": ["food", "goods"]}},
                           # cash kept for refunds: this share of our sales still in warranty (check.buy)
                           "reserve_share": {"type": "number", "minimum": 0, "maximum": 1}}},
        "restock": {                                     # optional: how restock picks its candidates
            "type": "object", "properties": {"per_asked_category": {"type": "integer", "minimum": 1, "maximum": 20},
                                             "stop_after": {"type": "number"}}},   # no buying from then on
        "buyer": {                                       # optional: targets for big goods orders
            "type": "object", "properties": {"big_list": {"type": "number", "minimum": 0},
                                             "target_share": {"type": "number", "minimum": 0, "maximum": 1},
                                             "open_share": {"type": "number", "minimum": 0, "maximum": 1},
                                             # user 11/3 23:4x: the same for food and smaller goods
                                             "food_target_share": {"type": "number", "minimum": 0, "maximum": 1},
                                             "food_open_share": {"type": "number", "minimum": 0, "maximum": 1}}},
        "repeat_buyer": {                                # optional: a buyer not on its first visit
            "type": "object", "properties": {"min_price": {"type": "string", "enum": ["list"]},
                                             "min_share_of_list": {"type": "number", "minimum": 0, "maximum": 1}}},
        "first_visit": {                                 # optional: what a buyer's first visit gets
            "type": "object", "properties": {"price": {"type": "string", "enum": ["at_cost"]}}},
        "certified_only": {"type": "boolean"},           # optional: sell only units with the market's certificate
        "welcome_food": {                                # optional: Welcome Food (restock brings it each supply window)
            "type": "object",
            "properties": {"each_supply_window": {"type": "boolean"}, "share_of_cost": {"type": "number"}}},
        "shop_card": {                                   # optional: what our A2A card tells other agents (name is fixed)
            "type": "object", "required": ["description"],
            "properties": {"description": {"type": "string", "minLength": 1, "maxLength": 600},
                           "examples": {"type": "array", "items": _STR}},
            "additionalProperties": False},
        "limits": LIMITS,
        "experiment": {"type": "object"},
        "delegation": {"type": "object"},
        "playbook": {                                    # optional (HARNESS 14, D34): the numbers the rules run on
            "type": "object", "properties": {
                # 11/5 03:1x: SKUs (or category words) on hold - the seller does not sell them, restock does not buy them
                "hold": {"type": "object", "properties": {
                    "skus": {"type": "array", "items": {"type": "string"}},
                    "words": {"type": "array", "items": {"type": "string"}}}},
                # 11/13 01:5x: maxes raised for volume (sergiobuilds bought 1,680 units a day, we 739)
                "restock": {"type": "object", "properties": {
                    "mode": {"type": "string", "enum": ["rules", "agent"]},
                    "cover_windows": {"type": "number", "minimum": 0.5, "maximum": 6},
                    "market_top": {"type": "integer", "minimum": 0, "maximum": 60},
                    "market_units": {"type": "integer", "minimum": 0, "maximum": 20},
                    # one line's share of the money the window may spend (breadth over depth), 1 = no cap
                    "max_line_share": {"type": "number", "minimum": 0.05, "maximum": 1},
                    "food_top": {"type": "integer", "minimum": 0, "maximum": 30},
                    "food_units": {"type": "integer", "minimum": 0, "maximum": 20},
                    "food_min_shelf_hours": {"type": "number", "minimum": 0},
                    "top_n": {"type": "integer", "minimum": 0, "maximum": 30},
                    "top_units": {"type": "integer", "minimum": 1, "maximum": 40},
                    "top_line_share": {"type": "number", "minimum": 0.05, "maximum": 1},
                    "units_cap": {"type": "integer", "minimum": 1, "maximum": 20},
                    "own_sales": {"type": "boolean"},
                    "own_floor": {"type": "boolean"},
                    "react": {"type": "boolean"},       # 11/10: the window's first run planned by the ReAct loop   # 11/10: own sales only raise goods lines (restock.TAPER_H)
                    "own_cover": {"type": "number", "minimum": 0.5, "maximum": 5},
                    "probe_units": {"type": "integer", "minimum": 0, "maximum": 5},
                    "food_cap": {"type": "integer", "minimum": 1, "maximum": 20},
                    # 11/5 09:0x: food bought for this many windows of its own sales; only fresh units count as held
                    "food_windows": {"type": "number", "minimum": 0.5, "maximum": 6},
                    "food_fresh_share": {"type": "number", "minimum": 0, "maximum": 0.95},
                    "food_old_windows": {"type": "number", "minimum": 0, "maximum": 12},
                    "food_min": {"type": "integer", "minimum": 0, "maximum": 20},
                    # 11/4 17:2x: the expected cost as a share of list, for planning (A units cost 55-75% of list)
                    "cost_share": {"type": "number", "minimum": 0.55, "maximum": 0.75},
                    # 11/13 10:4x: this many scarce SKUs (core.market_state) join the buy list first
                    "scarce_top": {"type": "integer", "minimum": 0, "maximum": 40}}},
                "seller": {"type": "object", "properties": {     # 11/4 02:5x: the seller's rules
                    "mode": {"type": "string", "enum": ["rules", "shadow", "agent"]},
                    "forged": {"type": "string"},        # 11/10: the B arm of a forged tool's A/B (harness.forge)
                    "open_share": {"type": "number", "minimum": 0.5, "maximum": 1.5},
                    "step_share": {"type": "number", "minimum": 0, "maximum": 0.3},
                    "min_margin": {"type": "number", "minimum": 0, "maximum": 1},
                    "accept_from_turn": {"type": "integer", "minimum": 1, "maximum": 12},
                    "clearance_hours": {"type": "number", "minimum": 1},
                    "hot_open_share": {"type": "number", "minimum": 0.5, "maximum": 1.5},
                    "hot_n": {"type": "integer", "minimum": 0, "maximum": 20},
                    "food_open_share": {"type": "number", "minimum": 0.5, "maximum": 1.5},   # 11/13: food opens here
                    "market_price": {"type": "boolean"},   # 11/13 10:4x: open/floor from the SKU's market (core.market_state)
                    # 11/13 11:2x: open where our acceptance curve says (core.price_curve), keeping deals: a bucket is chosen
                    # only with a deal rate of at least curve_min_p of the best, by rate x (margin + weight x price)
                    "curve": {"type": "boolean"},
                    "curve_volume_weight": {"type": "number", "minimum": 0, "maximum": 5},
                    "curve_min_p": {"type": "number", "minimum": 0, "maximum": 1},
                    "curve_explore": {"type": "number", "minimum": 0, "maximum": 0.5},
                    # 11/5 01:2x: food goes to a buyer only in a unit that lasts the hours it states
                    "food_fit": {"type": "boolean"},
                    # 11/5 02:4x: a buyer stating no hours gets food with at least this share of its shelf left
                    "food_unknown_share": {"type": "number", "minimum": 0.1, "maximum": 0.95},
                    # 11/5 02:5x: a food buyer stating no hours gets the freshest unit of the SKU
                    "food_fresh_unknown": {"type": "boolean"},
                    # 11/5 03:0x: a food buyer stating no hours gets a unit lasting the most hours reviews said were needed
                    "food_review_need": {"type": "boolean"}}},
                "review": {"type": "object", "properties": {     # 11/4 12:5x: the stars our buyer gives
                    "mode": {"type": "string", "enum": ["rules", "agent"]},
                    "base": {"type": "integer", "minimum": 1, "maximum": 5},
                    "five_share": {"type": "number", "minimum": 0, "maximum": 1}}},
                "buyer": {"type": "object", "properties": {      # 11/4 02:4x: the buyer's rules
                    "mode": {"type": "string", "enum": ["rules", "agent"]},
                    "ceiling_share": {"type": "number", "minimum": 0.3, "maximum": 1},
                    "max_shops_goods": {"type": "integer", "minimum": 1, "maximum": 30},
                    "max_shops_food": {"type": "integer", "minimum": 1, "maximum": 30},
                    "goods_time_budget": {"type": "number", "minimum": 5, "maximum": 250},
                    "food_time_budget": {"type": "number", "minimum": 5, "maximum": 250},
                    "goods_msg_timeout": {"type": "number", "minimum": 3, "maximum": 120},
                    "food_msg_timeout": {"type": "number", "minimum": 3, "maximum": 120},
                    # 11/4 21:1x: leave a food receipt whose claimed grade cannot last the order (design 1)
                    "shelf_guard": {"type": "boolean"},
                    # 11/5 01:4x: skip a shop whose last 3 receipts had no sig_seller (tried again after an hour)
                    "skip_unsigned": {"type": "boolean"},
                                          "min_pay_rate": {"type": "number", "minimum": 0, "maximum": 0.9},
                    # 11/6 22:5x: no deal whose expected saved is under our board average + value_margin
                    "value_gate": {"type": "boolean"},
                    "value_margin": {"type": "number", "minimum": -0.1, "maximum": 0.2},
                    # 11/7 11:xx: skip a shop+SKU the kit refused to pay for this many hours (0 = only 'not in stock')
                    "pay_refused_skip_h": {"type": "number", "minimum": 0, "maximum": 24},
                    # 11/7 11:xx: the negotiation shares, moved here from strategy.buyer (that stays a fallback)
                    "big_list": {"type": "number", "minimum": 0},
                    "open_share": {"type": "number", "minimum": 0, "maximum": 1},
                    "target_share": {"type": "number", "minimum": 0, "maximum": 1},
                    "food_open_share": {"type": "number", "minimum": 0, "maximum": 1},
                    "food_target_share": {"type": "number", "minimum": 0, "maximum": 1},
                    # 11/13 10:4x: aim at the SKU's market median, go first to shops that hold it (core.market_state)
                    "market_target": {"type": "boolean"}}}}},
    },
}

CURSOR = {
    "type": "object",
    "required": ["schema_version", "reader", "files"],
    "properties": {
        "schema_version": _VERSION,
        "reader": _STR,
        "files": {"type": "object"},      # relative path -> byte offset of the next unread line
        "last_event_id": {"type": ["string", "null"]},
    },
}

SCHEMAS = {"event": EVENT, "status": STATUS, "alert": ALERT, "strategy": STRATEGY, "cursor": CURSOR}
