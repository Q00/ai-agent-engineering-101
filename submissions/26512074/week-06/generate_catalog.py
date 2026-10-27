"""Generate the committed synthetic catalog deterministically, without a service.

Run: python generate_catalog.py [--output catalog.jsonl]
The six hand-described products are supplemented by 3,200 distinct variants.
Names, descriptions, attributes and reserves are fictional teaching data.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def build_catalog():
    baseline = [
        {"id": "lamp", "name": "lamp", "category": "lamp", "reserve": 100,
         "description": "A simple metal desk lamp for reading, 45 cm tall, neutral light with fixed brightness.",
         "attributes": {"material": "metal", "height_cm": 45, "dimmable": False, "usb_c": False}},
        {"id": "chair", "name": "chair", "category": "chair", "reserve": 180,
         "description": "A simple fabric office chair, 95 cm tall, with a fixed back and no footrest.",
         "attributes": {"material": "fabric", "height_cm": 95, "lumbar_support": False, "footrest": False}},
        {"id": "bookshelf", "name": "bookshelf", "category": "bookshelf", "reserve": 260,
         "description": "A wood bookshelf holding 60 books, 120 cm tall, for a home office.",
         "attributes": {"material": "wood", "height_cm": 120, "capacity_books": 60}},
        {"id": "oak-low-40", "name": "Low Oak Reading Shelf", "category": "bookshelf", "reserve": 270,
         "description": "A low wooden oak bookcase to keep about 40 books, 70 cm tall and below an ordinary 75 cm desk. Two shelves, 80 cm wide.",
         "attributes": {"material": "wood", "wood_species": "oak", "height_cm": 70, "width_cm": 80, "capacity_books": 40}},
        {"id": "amber-study-lamp", "name": "Amber USB-C Study Light", "category": "lamp", "reserve": 115,
         "description": "A small warm reading light with USB-C charging and adjustable brightness. Dimmable amber desk lamp, 32 cm tall, metal body.",
         "attributes": {"material": "metal", "height_cm": 32, "dimmable": True, "usb_c": True, "light_color": "warm"}},
        {"id": "mesh-back-support-chair", "name": "Mesh Lumbar Footrest Chair", "category": "chair", "reserve": 210,
         "description": "A breathable mesh seat with lumbar support, an extending footrest and adjustable height. Ergonomic office chair, 110 cm tall.",
         "attributes": {"material": "mesh", "height_cm": 110, "lumbar_support": True, "footrest": True, "adjustable_height": True}},
    ]
    yield from baseline
    materials = ("wood", "metal", "plastic", "fabric", "mesh")
    colors = ("black", "white", "blue", "green", "natural", "gray", "red", "brown")
    for category, base_reserve in (("lamp", 80), ("chair", 155), ("bookshelf", 230), ("desk", 300), ("storage", 125)):
        for index in range(640):
            material = materials[index % len(materials)]
            color = colors[(index // len(materials)) % len(colors)]
            height = {"lamp": 25, "chair": 85, "bookshelf": 65, "desk": 65, "storage": 45}[category] + (index % 13) * 5
            attrs = {"material": material, "color": color, "height_cm": height, "series": index // 40 + 1}
            description = f"A {color} {material} {category}, {height} cm tall, studio series {attrs['series']}. "
            if category == "bookshelf":
                attrs["capacity_books"] = (20, 30, 50, 60, 80)[index % 5]
                description += f"A bookcase for {attrs['capacity_books']} books, for reading rooms and office storage."
            elif category == "lamp":
                attrs.update(dimmable=index % 2 == 0, usb_c=False, light_color="neutral")
                description += "A reading light with " + ("adjustable brightness." if attrs["dimmable"] else "fixed brightness.")
            elif category == "chair":
                attrs.update(lumbar_support=index % 3 == 0, footrest=False, adjustable_height=index % 2 == 0)
                description += "An office seat with " + ("lumbar support." if attrs["lumbar_support"] else "a simple straight back.")
            elif category == "desk":
                attrs["width_cm"] = 80 + index % 8 * 10
                description += f"A work table {attrs['width_cm']} cm wide with room for a computer."
            else:
                attrs["capacity_liters"] = 20 + index % 10 * 5
                description += f"A cabinet organizer holding {attrs['capacity_liters']} liters of supplies."
            yield {"id": f"{category}-{index + 1:04d}", "name": f"{color.title()} {material.title()} {category.title()} {index + 1:04d}",
                   "category": category, "reserve": base_reserve + index % 17 * 3,
                   "description": description, "attributes": attrs}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("catalog.jsonl"))
    args = parser.parse_args()
    count = 0
    with args.output.open("w", encoding="utf-8", newline="\n") as output:
        for product in build_catalog():
            output.write(json.dumps(product, ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    print(json.dumps({"event": "catalog_generated", "products": count, "path": str(args.output)}))


if __name__ == "__main__":
    main()
