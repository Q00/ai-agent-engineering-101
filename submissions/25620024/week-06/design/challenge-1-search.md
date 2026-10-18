# Challenge 1 design, parts 2-3: understanding the request and finding candidates (v1, 2026-10-18)

Example request: "something wooden to keep about 40 books, not taller than my desk".
In the generated catalog 37 wooden bookshelves hold 36+ books and are at most 75 cm tall, so
both how the request is read and how the matches are ordered matter.

## Decisions (2026-10-18)

1. **The harness reads the request** (option A of three: harness / word list only / mixed).
   One harness call turns the buyer's sentence into a fixed JSON query, so wording that
   shares no word with the catalog ("somewhere to keep books" -> bookshelf) still works.
2. **Order by closeness to what the buyer said** (option A of three: closest / cheapest /
   most expensive). A buyer who asked for about 40 books sees 42-book shelves before
   144-book ones. Cheapest-first and upselling were rejected: the first ignores fit, the
   second risks "selling the wrong item".
3. **Five candidates** go to the curator (of 3 / 5 / 10).

## Part 2: the query the harness returns

```json
{"category": "bookshelf", "materials": ["oak", "pine", "walnut"], "color": null, "style": null,
 "max_height_cm": 75, "max_width_cm": null, "max_depth_cm": null,
 "capacity": 40, "rooms": [], "keywords": ["books", "wooden"]}
```

- `category` is one of the ten, or null if the request does not say.
- `materials` expands words like "wooden" to the catalog's woods (oak, pine, walnut).
- Relative sizes become numbers from typical sizes given in the prompt (a desk is about 75 cm
  high, a kitchen counter 90, a bed 50), so "not taller than my desk" -> `max_height_cm: 75`.
- `capacity` is the amount the buyer asked for, in the category's unit (books, seats, drawers,
  monitors, kg, lumens, metres of rail). The code, not the harness, decides the tolerance.
- The harness sees only the request and the schema: no catalog, no prices, no reserves.

## Part 3: the search (code only)

1. **Hard filters**: category; material in `materials`; each `max_*_cm`; capacity at least
   90% of the asked amount ("about 40 books" accepts 36+).
2. **Score** (lower is better), so the closest fit comes first:
   capacity distance (|log(item / asked)|, so 42 for 40 beats 144 for 40), plus small
   penalties when a stated colour, style or room does not match, minus a small bonus per
   keyword found in the description, features or rooms.
3. **Top five** by score; ties break by id so the order is reproducible.
4. **Nothing matches**: relax step by step and record what was relaxed (drop colour/style/room,
   then widen sizes by 10%, then drop the material list). Still nothing: the curator gets an
   empty list and asks the buyer one question.

`catalog_search.py "<request>"` prints the parsed query, the number of matches and the top five,
so parts 2-3 can be checked before the curator and the seller exist.
