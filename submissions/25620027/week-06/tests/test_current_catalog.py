from pathlib import Path
from typing import Final

import pytest
from pydantic import TypeAdapter, ValidationError

from generate_catalog import build_products
from market_a2a.catalog import Catalog, CatalogDataError, MissingProductError
from market_a2a.models import FrozenModel

ROOT: Final = Path(__file__).resolve().parents[1]


class ChallengeCase(FrozenModel):
    """Parse a descriptive query with its independent expected SKU."""

    case_id: str
    request: str
    expected_sku: str


class ShoppingOracle(FrozenModel):
    """Keep buyer descriptions separate from verification-only target identities."""

    request_id: str
    description: str
    expected_sku: str


class ShoppingFixture(FrozenModel):
    """Parse the persisted total budget and requested products."""

    budget: int
    items: tuple[ShoppingOracle, ...]


CASES: Final = TypeAdapter(tuple[ChallengeCase, ...]).validate_json(
    (ROOT / "challenge_cases.json").read_bytes(),
)
SHOPPING: Final = ShoppingFixture.model_validate_json(
    (ROOT / "shopping_list.json").read_bytes(),
)


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return Catalog(ROOT / "catalog.json")


def test_catalog_is_reproducible_when_generated(catalog: Catalog) -> None:
    # Given: the committed typed catalog.
    committed = catalog.products
    # When: generation is repeated.
    generated = build_products()
    # Then: every meaningful record is reproducible and distinct.
    assert generated == committed
    assert len(generated) >= 3000
    assert len({item.sku for item in generated}) == len(generated)
    assert len({item.name for item in generated}) == len(generated)
    assert len({item.description for item in generated}) == len(generated)
    assert {item.kind for item in generated} == {
        "lamp",
        "chair",
        "bookshelf",
        "desk",
        "storage",
    }
    assert min(item.reserve for item in generated) > 0


@pytest.mark.parametrize("case", CASES, ids=[case.case_id for case in CASES])
def test_description_finds_expected_product_when_name_is_omitted(
    catalog: Catalog,
    case: ChallengeCase,
) -> None:
    # Given: an oracle whose product name is absent from the buyer request.
    expected = catalog.get(case.expected_sku)
    assert expected.name.lower() not in case.request.lower()
    # When: the description is searched against thousands of products.
    results = catalog.search(case.request)
    # Then: the target ranks first and the model gets a bounded shortlist.
    assert results[0].sku == case.expected_sku
    assert 1 <= len(results) <= 6
    print(
        f"case={case.case_id} expected={case.expected_sku}",
        f"candidates={[item.sku for item in results]}",
    )


@pytest.mark.parametrize(
    "item", SHOPPING.items, ids=[item.request_id for item in SHOPPING.items]
)
def test_shopping_description_finds_product_in_specialist_shop(
    catalog: Catalog,
    item: ShoppingOracle,
) -> None:
    # Given: a specialist shop with a distinct price offset.
    expected = catalog.get(item.expected_sku)
    shop = Catalog(ROOT / "catalog.json", price_offset=20, kinds=(expected.kind,))
    # When: only the description is searched.
    results = shop.search(item.description)
    # Then: the requested product remains discoverable at this shop's reserve.
    assert results[0].sku == item.expected_sku
    assert results[0].reserve == expected.reserve + 20
    assert all(result.kind == expected.kind for result in results)


def test_shopping_fits_total_budget_when_all_targets_are_selected(
    catalog: Catalog,
) -> None:
    # Given: five independently requested products.
    items = SHOPPING.items
    # When: their actual catalog reserves are totaled.
    total = sum(catalog.get(item.expected_sku).reserve for item in items)
    # Then: there is negotiation headroom without duplicate products.
    assert len(items) == len({item.request_id for item in items}) == 5
    assert len({item.expected_sku for item in items}) == 5
    assert {catalog.get(item.expected_sku).kind for item in items} == {
        "lamp",
        "chair",
        "bookshelf",
        "desk",
        "storage",
    }
    assert total == 1250
    assert total * 1.5 < SHOPPING.budget == 2200
    print(f"shopping_count={len(items)} reserves={total} budget={SHOPPING.budget}")


@pytest.mark.parametrize(
    ("query", "reserve"), [("lamp", 100), ("chair", 180), ("bookshelf", 260)]
)
def test_baseline_remains_first_when_only_item_is_named(
    catalog: Catalog, query: str, reserve: int
) -> None:
    # Given: the trade test names a baseline item.
    request = f"I want to buy a {query}"
    # When: the request is searched.
    results = catalog.search(request)
    # Then: its original SKU and reserve remain compatible.
    assert results[0].sku == query
    assert results[0].name == query
    assert results[0].reserve == reserve


def test_constraints_are_hard_limits_when_generic_variants_compete(
    catalog: Catalog,
) -> None:
    # Given: many wooden bookshelves differ in capacity and height.
    request = "brown wooden bookcase for at least 40 books, no taller than 75 cm"
    # When: retrieval applies explicit attributes.
    results = catalog.search(request)
    # Then: every returned candidate satisfies every requested attribute.
    assert results
    assert all(
        item.kind == "bookshelf"
        and item.color == "brown"
        and item.material == "wood"
        and item.height_cm <= 75
        and item.capacity >= 40
        for item in results
    )


@pytest.mark.parametrize(
    "query", ["purple helicopter", "black wooden bookshelf for 9000 books", ""]
)
def test_search_is_empty_when_no_product_satisfies_request(
    catalog: Catalog, query: str
) -> None:
    # Given: a description that cannot match the inventory.
    # When: the catalog is searched.
    results = catalog.search(query)
    # Then: unrelated fallback products are not fabricated.
    assert results == ()


def test_search_stays_bounded_when_caller_requests_everything(catalog: Catalog) -> None:
    # Given: a broad request and an oversized requested limit.
    # When: retrieval is called.
    results = catalog.search("lamp", limit=3000)
    # Then: no full catalog dump can reach the model.
    assert len(results) == 6


def test_get_raises_typed_error_when_sku_is_absent(catalog: Catalog) -> None:
    # Given: an absent SKU.
    # When: exact lookup is attempted.
    with pytest.raises(MissingProductError) as raised:
        _ = catalog.get("missing-sku")
    # Then: the typed error preserves the requested identity.
    assert raised.value.sku == "missing-sku"


def test_catalog_rejects_duplicates_when_loading(
    tmp_path: Path, catalog: Catalog
) -> None:
    # Given: duplicate identities in persisted data.
    path = tmp_path / "catalog.json"
    _ = path.write_text(
        f"[{catalog.products[0].model_dump_json()},{catalog.products[0].model_dump_json()}]"
    )
    # When: the boundary parses the catalog.
    with pytest.raises(CatalogDataError, match="Duplicate"):
        _ = Catalog(path)
    # Then: construction fails instead of silently replacing a record.


def test_catalog_rejects_invalid_records_when_loading(tmp_path: Path) -> None:
    # Given: a persisted record with a nonpositive reserve.
    path = tmp_path / "catalog.json"
    _ = path.write_text(
        '[{"sku":"broken","name":"broken","description":"broken","kind":"lamp","reserve":0}]'
    )
    # When: the boundary parses the catalog.
    with pytest.raises(ValidationError):
        _ = Catalog(path)
    # Then: invalid external data never becomes a Product.
