from pathlib import Path
from market_a2a.catalog import Catalog
from market_a2a.seller import create_app
from market_a2a.seller_offer import accepts_quote, bargaining_offer, basic_request

catalog = Catalog(Path("/app/catalog.json"))
assert len(catalog.products) == 3008
assert basic_request("I want a lamp. Do you provide a flexible refund policy?") == "lamp"
assert basic_request("A black dimmable lamp with flexible neck, no taller than 30 cm.") is None
assert accepts_quote("I accept")
assert not accepts_quote("I accept if you guarantee a refund")
assert bargaining_offer("I offer 110 if you guarantee quality") is None
assert [catalog.get(x).reserve for x in ("lamp", "chair", "bookshelf")] == [100, 180, 260]
assert callable(create_app)
print("PASS: isolated image imports, basic/descriptive routing, assent and conditional-offer guards")
