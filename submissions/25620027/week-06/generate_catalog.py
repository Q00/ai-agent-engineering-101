#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["pydantic>=2.12,<3"]
# ///
# ─── How to run ───
# Install uv: curl -LsSf https://astral.sh/uv/install.sh | sh
# Run: uv run --python 3.12.11 generate_catalog.py
# Or: chmod +x generate_catalog.py && ./generate_catalog.py
# ──────────────────
"""Reproduce a furniture catalog from meaningful attribute combinations."""

from __future__ import annotations

from itertools import product
from pathlib import Path
from typing import Final

from pydantic import TypeAdapter

from market_a2a.models import Product

KINDS: Final = ("lamp", "chair", "bookshelf", "desk", "storage")
MATERIALS: Final = ("wood", "steel", "bamboo", "aluminum", "plastic")
COLORS: Final = ("black", "white", "brown", "blue", "green", "gray")
FEATURES: Final = {
    "lamp": ("ambient shade", "task lighting", "bedside glow", "floor lighting"),
    "chair": ("dining seat", "visitor seat", "stackable seat", "lounge seat"),
    "bookshelf": (
        "open shelves",
        "display shelves",
        "adjustable shelves",
        "wall shelves",
    ),
    "desk": ("writing surface", "computer workspace", "craft table", "study surface"),
    "storage": (
        "drawer organizer",
        "closed cabinet",
        "utility chest",
        "office cupboard",
    ),
}
BASE_HEIGHT: Final = {
    "lamp": 28,
    "chair": 78,
    "bookshelf": 60,
    "desk": 68,
    "storage": 45,
}
BASE_RESERVE: Final = {
    "lamp": 100,
    "chair": 180,
    "bookshelf": 260,
    "desk": 300,
    "storage": 200,
}


def build_products() -> tuple[Product, ...]:
    """Generate 3,000 distinct variants plus baseline and curated products."""
    products = [
        Product(
            sku="lamp",
            name="lamp",
            description="Basic white task lamp.",
            kind="lamp",
            material="steel",
            color="white",
            height_cm=45,
            reserve=100,
        ),
        Product(
            sku="chair",
            name="chair",
            description="Basic brown dining chair.",
            kind="chair",
            material="wood",
            color="brown",
            height_cm=90,
            capacity=1,
            reserve=180,
        ),
        Product(
            sku="bookshelf",
            name="bookshelf",
            description="Basic tall white bookshelf for 80 books.",
            kind="bookshelf",
            material="wood",
            color="white",
            height_cm=180,
            capacity=80,
            reserve=260,
        ),
        Product(
            sku="lamp-nocturne",
            name="Nocturne Mini",
            kind="lamp",
            material="steel",
            color="black",
            height_cm=30,
            capacity=0,
            reserve=120,
            description="""Small black reading lamp with a dimmable warm glow
and flexible neck; 30 cm tall for a bedside table.""".replace("\n", " "),
        ),
        Product(
            sku="bookshelf-meadow",
            name="Meadow Low",
            kind="bookshelf",
            material="wood",
            color="brown",
            height_cm=70,
            capacity=40,
            reserve=280,
            description="""Low wooden bookcase holding 40 books, 70 cm tall,
fits beneath a standard 75 cm desk; rounded corners.""".replace("\n", " "),
        ),
        Product(
            sku="chair-stratus",
            name="Stratus Support",
            kind="chair",
            material="steel",
            color="gray",
            height_cm=110,
            capacity=1,
            reserve=250,
            description="""Gray ergonomic office chair with breathable mesh,
adjustable lumbar support and adjustable armrests for long work sessions.""".replace(
                "\n",
                " ",
            ),
        ),
        Product(
            sku="desk-harbor",
            name="Harbor Worktop",
            kind="desk",
            material="bamboo",
            color="brown",
            height_cm=75,
            capacity=2,
            reserve=360,
            description="""Brown bamboo computer desk, 75 cm tall,
with integrated cable tray and dual monitor workspace.""".replace("\n", " "),
        ),
        Product(
            sku="storage-cove",
            name="Cove Keeper",
            kind="storage",
            material="steel",
            color="blue",
            height_cm=65,
            capacity=60,
            reserve=240,
            description="""Blue steel storage cabinet with lockable doors
and 60 litre capacity, 65 cm tall, for secure office supplies.""".replace("\n", " "),
        ),
    ]
    for kind, material, color, variant in product(KINDS, MATERIALS, COLORS, range(20)):
        height = BASE_HEIGHT[kind] + variant * 3
        capacity = {
            "lamp": 0,
            "chair": 1,
            "bookshelf": 20 + variant * 5,
            "desk": 1 + variant % 3,
            "storage": 20 + variant * 10,
        }[kind]
        feature = FEATURES[kind][variant % 4]
        unit = {
            "lamp": "light sources",
            "chair": "seat",
            "bookshelf": "books",
            "desk": "monitors",
            "storage": "litres",
        }[kind]
        products.append(
            Product(
                sku=f"{kind}-{material}-{color}-{variant + 1:02}",
                name=f"{color.title()} {material.title()} {kind.title()} {height}",
                description=" ".join(
                    (
                        f"{color.title()} {material} {kind} with {feature};",
                        f"{height} cm tall, capacity {capacity} {unit}.",
                    )
                ),
                kind=kind,
                material=material,
                color=color,
                height_cm=height,
                capacity=capacity,
                reserve=BASE_RESERVE[kind]
                + variant * 7
                + MATERIALS.index(material) * 11
                + COLORS.index(color) * 3,
            )
        )
    return tuple(products)


def main() -> None:
    """Write deterministic UTF-8 catalog JSON beside this script."""
    payload = TypeAdapter(tuple[Product, ...]).dump_json(build_products(), indent=2)
    _ = Path(__file__).with_name("catalog.json").write_bytes(payload + b"\n")


if __name__ == "__main__":
    main()
