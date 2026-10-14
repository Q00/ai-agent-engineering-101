"""Generate the reproducible synthetic product catalog used by challenge 1."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

OUTPUT = Path(__file__).resolve().parent / "data" / "catalog.json"
MATERIAL_KO = {
    "wood": "나무 원목", "metal": "금속 철제", "bamboo": "대나무",
    "plastic": "플라스틱", "composite": "합성목", "glass": "유리",
    "fabric": "직물 패브릭", "leather": "가죽", "ceramic": "세라믹 도자기",
}
CONFIG = {
    "bookshelf": {"ko": "책장", "materials": ("wood", "metal", "bamboo", "composite", "plastic", "glass"), "base": 170},
    "chair": {"ko": "의자", "materials": ("wood", "metal", "bamboo", "plastic", "fabric", "leather"), "base": 95},
    "lamp": {"ko": "램프 조명", "materials": ("metal", "wood", "ceramic", "glass", "plastic", "bamboo"), "base": 65},
    "desk": {"ko": "책상", "materials": ("wood", "metal", "bamboo", "glass", "composite", "plastic"), "base": 190},
    "table": {"ko": "식탁 테이블", "materials": ("wood", "metal", "bamboo", "glass", "composite", "plastic"), "base": 180},
    "cabinet": {"ko": "수납장", "materials": ("wood", "metal", "bamboo", "composite", "plastic", "glass"), "base": 160},
    "sofa": {"ko": "소파", "materials": ("fabric", "leather", "fabric", "leather", "fabric", "leather"), "base": 330},
    "bed": {"ko": "침대", "materials": ("wood", "metal", "bamboo", "composite", "wood", "metal"), "base": 290},
    "stool": {"ko": "스툴", "materials": ("wood", "metal", "bamboo", "plastic", "fabric", "leather"), "base": 55},
    "bench": {"ko": "벤치", "materials": ("wood", "metal", "bamboo", "plastic", "fabric", "leather"), "base": 120},
}
FINISHES = ("natural", "charcoal", "white", "sand", "forest")


def _base_items() -> list[dict[str, Any]]:
    return [
        {
            "id": "lamp", "name": "Classic Reading Lamp / 기본 독서 램프",
            "description": "A metal desk lamp with a warm LED, adjustable shade and stable weighted base. 금속 독서 조명, 높이 45cm, 각도 조절 갓과 따뜻한 LED가 있습니다.",
            "reserve": 100, "category": "lamp",
            "attributes": {"material": "metal", "height_cm": 45, "width_cm": 18, "depth_cm": 18, "power_watts": 8, "finish": "charcoal"},
        },
        {
            "id": "chair", "name": "Classic Dining Chair / 기본 원목 의자",
            "description": "A solid oak dining chair with a curved backrest and a 45cm seat height. 참나무 원목 의자, 전체 높이 85cm, 좌면 높이 45cm, 안정적인 등받이가 있습니다.",
            "reserve": 180, "category": "chair",
            "attributes": {"material": "wood", "wood_species": "oak", "height_cm": 85, "seat_height_cm": 45, "width_cm": 45, "depth_cm": 45, "capacity_people": 1, "finish": "natural"},
        },
        {
            "id": "bookshelf", "name": "Classic Oak Bookshelf / 기본 원목 책장",
            "description": "A wooden oak bookshelf for approximately 40 standard books, with three shelves. 나무 원목 책장, 높이 90cm, 너비 60cm, 깊이 28cm, 책 40권을 보관하는 3단 선반입니다.",
            "reserve": 260, "category": "bookshelf",
            "attributes": {"material": "wood", "wood_species": "oak", "height_cm": 90, "width_cm": 60, "depth_cm": 28, "capacity_books": 40, "shelves_count": 3, "finish": "natural"},
        },
    ]


def _dimensions(category: str, index: int) -> dict[str, Any]:
    size = index % 10
    width_variant = index // 60
    if category == "bookshelf":
        height = (60, 70, 75, 80, 90, 100, 120, 150, 180, 200)[size]
        width = (60, 75, 90, 105, 120)[width_variant]
        shelves = max(2, height // 30)
        return {"height_cm": height, "width_cm": width, "depth_cm": 28 + 2 * width_variant,
                "shelves_count": shelves, "capacity_books": shelves * (width // 3)}
    if category in {"chair", "stool", "bench"}:
        seat_height = (40, 42, 44, 45, 46, 48, 50, 55, 60, 65)[size]
        height = seat_height + (40 if category == "chair" else 0)
        width = (42 + width_variant * 3) if category != "bench" else (90 + width_variant * 20)
        return {"height_cm": height, "width_cm": width, "depth_cm": 40 + width_variant * 2,
                "seat_height_cm": seat_height, "capacity_people": 1 if category != "bench" else max(2, width // 45)}
    if category == "lamp":
        return {"height_cm": 25 + size * 5, "width_cm": 15 + width_variant * 3,
                "depth_cm": 15 + width_variant * 3, "power_watts": 5 + size,
                "color_temperature_k": (2700, 3000, 4000, 5000, 6500)[width_variant]}
    if category in {"desk", "table"}:
        return {"height_cm": (65, 68, 70, 72, 73, 74, 75, 76, 78, 80)[size],
                "width_cm": 90 + width_variant * 20, "depth_cm": 50 + width_variant * 10,
                "capacity_people": 1 if category == "desk" else 2 + width_variant}
    if category == "cabinet":
        return {"height_cm": 60 + size * 15, "width_cm": 50 + width_variant * 15,
                "depth_cm": 35 + width_variant * 5, "compartments": 2 + size // 2}
    if category == "sofa":
        return {"height_cm": 75 + size * 2, "width_cm": 120 + width_variant * 30,
                "depth_cm": 80 + size, "capacity_people": 2 + width_variant // 2}
    return {"height_cm": 25 + size * 3, "width_cm": 90 + width_variant * 30,
            "depth_cm": 190 + (size % 3) * 5, "capacity_people": 1 if width_variant < 2 else 2}


def build_catalog() -> list[dict[str, Any]]:
    """Three prescribed products plus 300 variants in ten product categories."""
    items = _base_items()
    for category, config in CONFIG.items():
        for index in range(300):
            material = config["materials"][(index // 10) % 6]
            finish = FINISHES[(index // 60) % len(FINISHES)]
            attrs = _dimensions(category, index)
            attrs.update({"material": material, "finish": finish})
            if material == "wood":
                attrs["wood_species"] = ("oak", "pine", "walnut")[index % 3]
            size_label = "Compact" if attrs["height_cm"] <= 80 else "Standard" if attrs["height_cm"] <= 120 else "Tall"
            description = (
                f"{size_label} {material} {category} with a {finish} finish. "
                f"Height {attrs['height_cm']}cm, width {attrs['width_cm']}cm, depth {attrs['depth_cm']}cm. "
                f"{MATERIAL_KO[material]} {config['ko']}, 높이 {attrs['height_cm']}cm, "
                f"너비 {attrs['width_cm']}cm, 깊이 {attrs['depth_cm']}cm. "
            )
            if category == "bookshelf":
                description += (
                    f"{attrs['shelves_count']} open shelves hold {attrs['capacity_books']} standard books; "
                    f"a back panel helps keep books upright. {attrs['shelves_count']}단 선반, "
                    f"책 {attrs['capacity_books']}권 수납, 뒤판으로 책을 안정적으로 보관합니다."
                )
            elif category in {"chair", "stool", "bench"}:
                description += (
                    f"Seat height {attrs['seat_height_cm']}cm, room for {attrs['capacity_people']} people. "
                    f"좌면 높이 {attrs['seat_height_cm']}cm, {attrs['capacity_people']}인용으로 주방과 거실에서 사용합니다."
                )
            elif category == "lamp":
                description += (
                    f"{attrs['power_watts']}W LED, {attrs['color_temperature_k']}K light, adjustable shade. "
                    f"각도 조절 갓과 {attrs['power_watts']}W LED로 독서와 작업에 사용합니다."
                )
            elif category == "cabinet":
                description += f"{attrs['compartments']} enclosed compartments organize supplies. {attrs['compartments']}개 수납칸에 생활용품을 정리합니다."
            elif category == "bed":
                description += f"A ventilated slatted frame for {attrs['capacity_people']} people, mattress excluded. {attrs['capacity_people']}인용 통풍 프레임이며 매트리스는 별도입니다."
            elif category == "sofa":
                description += f"Removable seat cushions, room for {attrs['capacity_people']} people. 분리형 방석과 {attrs['capacity_people']}인용 좌석으로 거실에서 사용합니다."
            else:
                description += f"A flat working surface for {attrs['capacity_people']} people with rounded corners. 둥근 모서리와 {attrs['capacity_people']}인용 상판으로 실내에서 사용합니다."
            items.append({
                "id": f"{category}-{index + 1:04d}",
                "name": f"{size_label} {material.title()} {category.title()} {index + 1:04d} / {config['ko']} {index + 1:04d}",
                "description": description,
                "reserve": config["base"] + attrs["width_cm"] // 4 + attrs["height_cm"] // 5 + (index // 10 % 6) * 11,
                "category": category, "attributes": attrs,
            })
    return items


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    items = build_catalog()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "items": len(items), "categories": len(CONFIG)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
