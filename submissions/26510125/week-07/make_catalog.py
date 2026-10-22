"""Week 06 — generate catalog.json: ~2,400 furniture/home items with a name,
a description, and a reserve price. Deterministic (seeded), so the committed
file can be regenerated. The three trade-test items (lamp, chair, bookshelf)
are present under exactly those names with reserves 100, 180, 260.
"""
import itertools
import json
import random

random.seed(606)

BASE = {  # item type -> (base reserve, description template, size words)
    "lamp": (100, "{adj} {mat} desk lamp, {h} cm tall, {light} light, {extra}", ["for a desk", "for reading", "for a bedside table"]),
    "chair": (180, "{adj} {mat} chair, seat height {h} cm, {extra}", ["for a desk", "for dining", "for a reading corner"]),
    "bookshelf": (260, "{adj} {mat} bookshelf with {shelves} shelves, {h} cm tall, holds about {books} books, {extra}", ["for an office", "for a living room", "for a kids' room"]),
    "desk": (320, "{adj} {mat} desk, {w} cm wide, {h} cm tall, {extra}", ["for a home office", "for a student", "with a drawer"]),
    "table": (240, "{adj} {mat} table, {w} cm wide, seats {seats}, {extra}", ["for dining", "for a kitchen", "for a balcony"]),
    "sofa": (520, "{adj} {mat} sofa, {seats}-seater, {w} cm wide, {extra}", ["for a living room", "convertible to a bed", "with storage"]),
    "cabinet": (300, "{adj} {mat} cabinet with {shelves} shelves and doors, {h} cm tall, {extra}", ["for a kitchen", "for a bathroom", "for a hallway"]),
    "stool": (70, "{adj} {mat} stool, seat height {h} cm, {extra}", ["for a bar", "for a kitchen counter", "for a workshop"]),
    "mirror": (90, "{adj} {mat}-framed wall mirror, {h} cm tall, {extra}", ["for a hallway", "for a bedroom", "for a bathroom"]),
    "rug": (150, "{adj} {mat} rug, {w} by {h} cm, {extra}", ["for a living room", "for a bedroom", "for a hallway"]),
    "bed frame": (480, "{adj} {mat} bed frame, {w} cm wide, {extra}", ["for a queen mattress", "for a single mattress", "with under-bed storage"]),
    "nightstand": (120, "{adj} {mat} nightstand with {shelves} drawer(s), {h} cm tall, {extra}", ["for a bedroom", "with a USB port", "with an open shelf"]),
}
ADJ = ["compact", "tall", "low", "narrow", "wide", "sturdy", "lightweight", "foldable", "vintage", "modern", "minimalist", "industrial", "rustic", "sleek"]
MAT = ["oak", "walnut", "pine", "bamboo", "steel", "aluminum", "rattan", "velvet", "linen", "glass", "marble", "wool", "cotton", "birch"]
LIGHT = ["warm", "cool", "dimmable", "LED", "adjustable"]

items = [  # the trade-test items first, under exactly their names
    {"id": "I0001", "name": "lamp", "description": "desk lamp, warm light, adjustable arm", "reserve": 100},
    {"id": "I0002", "name": "chair", "description": "wooden chair, seat height 45 cm, for a desk", "reserve": 180},
    {"id": "I0003", "name": "bookshelf", "description": "wooden bookshelf with 5 shelves, 180 cm tall, holds about 150 books", "reserve": 260},
]
n = len(items) + 1
for kind, (base, tmpl, extras) in BASE.items():
    for adj, mat in itertools.product(ADJ, MAT):
        h = random.choice([40, 45, 60, 75, 90, 110, 120, 150, 180, 200])
        shelves = random.choice([2, 3, 4, 5, 6])
        desc = tmpl.format(adj=adj, mat=mat, h=h, w=random.choice([60, 80, 100, 120, 140, 160, 180, 200]),
                           light=random.choice(LIGHT), shelves=shelves, books=shelves * random.choice([25, 30, 40]),
                           seats=random.choice([2, 3, 4, 6]), extra=random.choice(extras))
        reserve = int(base * random.uniform(0.6, 1.8) / 5) * 5
        items.append({"id": f"I{n:04d}", "name": f"{adj} {mat} {kind}", "description": desc, "reserve": max(20, reserve)})
        n += 1

json.dump(items, open("catalog.json", "w", encoding="utf-8"), ensure_ascii=False, indent=0)
print(f"wrote catalog.json with {len(items)} items")
