# Challenge 1 design, part 1: the catalog (draft v1, 2026-10-18)

Status: draft for review. Nothing is generated yet.

## Why the catalog comes first

Every later part searches the catalog by its fields: the request parser (part 2) turns a
buyer's description into conditions on these fields, and the search (part 3) filters on them.
A field that is not in the catalog cannot be searched, so the fields are decided first.

## Categories (10)

| category | subtypes | main capacity field | example buyer wording |
|---|---|---|---|
| bookshelf | open, closed-door, ladder, cube | `books` | "somewhere to keep about 40 books" |
| desk | writing, computer, standing, corner | `width_cm` (work surface) | "a desk big enough for two monitors" |
| office chair | task, mesh, gaming | `max_load_kg` | "a chair I can sit on all day" |
| dining chair | wooden, upholstered, stackable | `seats` (=1, sold singly) | "chairs for the dining table" |
| sofa | 2-seat, 3-seat, sofa bed, corner | `seats` | "a couch for three people" |
| bed | single, double, queen, bunk | `sleeps` | "a bed for a small kids' room" |
| wardrobe | 1-door, 2-door, sliding | `hanging_m` (rail length) | "a closet for winter coats" |
| chest of drawers | 3-drawer, 5-drawer, wide | `drawers` | "something with drawers for clothes" |
| table | dining, coffee, side, folding | `seats` (dining) / `width_cm` | "a table that seats six" |
| lamp | desk, floor, pendant, clip | `lumens` | "a light for reading in bed" |

The trade test's three items (lamp, chair, bookshelf) stay in the plain trade-test seller;
see "Open decisions".

## Fields per item

| field | type | example | used by |
|---|---|---|---|
| `id` | string | `BS-0042` | everything (the deal is locked to an id) |
| `name` | string | `Oak 5-Shelf Bookcase 80` | buyer-facing reply |
| `category` | enum (10 above) | `bookshelf` | hard filter |
| `subtype` | string | `open` | soft match |
| `material` | enum | `oak`, `pine`, `walnut`, `metal`, `particleboard`, `fabric`, `leather`, `glass` | hard or soft filter |
| `color` | string | `natural`, `white`, `black`, `grey` | soft match |
| `style` | string | `scandinavian`, `industrial`, `classic`, `modern` | soft match |
| `width_cm`, `depth_cm`, `height_cm` | int | 80, 30, 72 | hard filter ("lower than my desk" -> height_cm <= 75) |
| capacity (one per category) | int | `books: 42` | hard filter ("about 40 books" -> books >= 36) |
| `features` | list | `adjustable shelves`, `wheels`, `doors` | soft match |
| `rooms` | list | `study`, `living room`, `kids room` | soft match (the IKEA idea: where it goes) |
| `description` | string, one or two sentences | generated from the fields | keyword search, shown to the curator |
| `reserve` | int | 180 | **code guard only; never shown to the buyer** |
| `list_price` | int | 259 | the opening ask can start near this |

## How it is generated (code, reproducible)

- A small script builds every item from combinations: subtype x material x size preset x color x style,
  with a fixed random seed so the same catalog comes out every time.
- About 400-500 items per category, about 4,500 in total. Committed as `catalog.json` with the script.
- Capacity follows the size, so the numbers stay plausible: for a bookshelf,
  `books = shelves x floor(width_cm / 2.5)` (about one book per 2.5 cm of shelf).
- Reserve follows a formula, so it can be checked: `base(category) x material factor x size factor`,
  rounded to 5. `list_price` is about 1.3-1.6 x reserve.
- The description is a template filled from the fields, for example:
  "Natural oak open bookcase with 5 adjustable shelves, holds about 42 books. W80 x D30 x H72 cm.
   Scandinavian style, good for a study or living room."
- Near-duplicates are on purpose (the same bookcase in oak and in pine, 72 cm and 180 cm tall), so
  picking the right item actually depends on reading the description.

## Sample items (what five rows will look like)

```json
[
  {"id": "BS-0042", "name": "Oak 5-Shelf Bookcase 80", "category": "bookshelf", "subtype": "open",
   "material": "oak", "color": "natural", "style": "scandinavian",
   "width_cm": 80, "depth_cm": 30, "height_cm": 72, "books": 42,
   "features": ["adjustable shelves"], "rooms": ["study", "living room"],
   "description": "Natural oak open bookcase with 5 adjustable shelves, holds about 42 books. W80 x D30 x H72 cm. Scandinavian style, good for a study or living room.",
   "reserve": 180, "list_price": 259},
  {"id": "BS-0043", "name": "Pine 5-Shelf Bookcase 80", "category": "bookshelf", "subtype": "open",
   "material": "pine", "color": "natural", "style": "scandinavian",
   "width_cm": 80, "depth_cm": 30, "height_cm": 72, "books": 42,
   "features": ["adjustable shelves"], "rooms": ["study", "living room"],
   "description": "Natural pine open bookcase ...", "reserve": 120, "list_price": 179},
  {"id": "BS-0107", "name": "Walnut Tall Bookcase 180", "category": "bookshelf", "subtype": "closed-door",
   "material": "walnut", "color": "brown", "style": "classic",
   "width_cm": 90, "depth_cm": 35, "height_cm": 180, "books": 144,
   "features": ["glass doors"], "rooms": ["study"],
   "description": "...", "reserve": 420, "list_price": 619},
  {"id": "DK-0210", "name": "White Standing Desk 140", "category": "desk", "subtype": "standing",
   "material": "particleboard", "color": "white", "style": "modern",
   "width_cm": 140, "depth_cm": 70, "height_cm": 125, "features": ["electric height adjust"],
   "rooms": ["study", "office"], "description": "...", "reserve": 310, "list_price": 449},
  {"id": "LP-0033", "name": "Brass Clip Reading Lamp", "category": "lamp", "subtype": "clip",
   "material": "metal", "color": "brass", "style": "industrial",
   "width_cm": 12, "depth_cm": 12, "height_cm": 35, "lumens": 400,
   "features": ["clip", "warm light"], "rooms": ["bedroom"],
   "description": "...", "reserve": 25, "list_price": 39}
]
```

For the example request "something wooden to keep about 40 books, not taller than my desk",
BS-0042 and BS-0043 both match (wood, 42 books, 72 cm); BS-0107 does not (180 cm).
The curator then has a real choice; if the buyer said nothing about price or wood type, either
is a correct item, and the deal is locked to the one the shop offers.

## Open decisions

1. **One seller or two.** Keep `seller.py` as it is for the trade test (three plain items) and
   write a separate `catalog_seller.py` for challenge 1, or add the catalog to `seller.py` with a
   fast path for "lamp / chair / bookshelf". A separate program keeps the passing trade test safe.
2. **How strict "the right item" is.** When two items match equally (oak vs pine above), the
   shop offers one and says which; when the description is ambiguous (no size, no capacity),
   the shop asks one clarifying question instead of guessing.
3. **Units and language.** Centimetres and whole-number prices, English item text (the buyer
   agents and the trade test speak English).
