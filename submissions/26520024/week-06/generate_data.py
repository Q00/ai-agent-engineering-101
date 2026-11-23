"""Deterministic fictional catalog and predeclared challenge fixtures."""
import argparse
import json
from pathlib import Path
import random

CATEGORIES = ['lamp', 'chair', 'bookshelf', 'desk', 'cabinet', 'sofa', 'stool', 'table', 'screen', 'rug']
RESERVES = [100, 180, 260, 180, 200, 360, 75, 220, 140, 90]
SPECIAL = [
    dict(id='atlas', name='Atlas low bookcase', category='bookshelf', reserve=210,
         description='Blue wooden bookcase for 40 books. Height 80 cm, width 60 cm. Fits below a desk. Adjustable shelves.'),
    dict(id='ember', name='Ember task light', category='lamp', reserve=105,
         description='Red metal desk light with USB power, adjustable arm and 800 lumens. Suitable for close reading.'),
    dict(id='fern', name='Fern office seat', category='chair', reserve=190,
         description='Green fabric ergonomic office seat with lumbar support, armrests and a 120 kg load rating.'),
    dict(id='slate', name='Slate folding workstation', category='desk', reserve=230,
         description='Black steel folding workstation. Width 120 cm, height 70 cm. Folds flat for storage in a small studio.'),
    dict(id='cloud', name='Cloud compact couch', category='sofa', reserve=310,
         description='White linen two-person couch, width 145 cm. Removable washable covers and wooden legs.'),
    dict(id='reed', name='Reed privacy divider', category='screen', reserve=135,
         description='Natural bamboo three-panel privacy divider, height 170 cm. Foldable, freestanding and lightweight.'),
]
QUERIES = [
    'I need something blue and wooden to keep about 40 books, no taller than 80 cm, with adjustable shelves.',
    'I need a red metal reading light with USB power, an adjustable arm, and 800 lumens.',
    'Find green fabric seating for office work, with lumbar support, armrests, and support for 120 kg.',
    'I need a black steel workspace that folds flat, 120 cm wide and 70 cm high, for a small studio.',
    'Find a white linen seat for two people, about 145 cm wide, with washable covers and wooden legs.',
    'I need a lightweight natural bamboo privacy partition with three folding panels, 170 cm tall.',
]


def build():
    rng = random.Random(26520024)
    catalog = [dict(id=category, name=category, category=category, reserve=reserve,
                    description='Standard household {} for everyday use.'.format(category))
               for category, reserve in zip(CATEGORIES, RESERVES)]
    catalog += SPECIAL
    materials = ['oak', 'pine', 'aluminum', 'plastic', 'leather', 'velvet']
    colors = ['yellow', 'purple', 'orange', 'brown', 'gray', 'silver']
    for i in range(3000):
        category = CATEGORIES[i % len(CATEGORIES)]
        material, color = rng.choice(materials), rng.choice(colors)
        height, width = rng.randrange(30, 201), rng.randrange(35, 181)
        features = rng.choice(['compact apartment design', 'heavy-duty commercial construction',
                               'outdoor weather-resistant finish', 'rounded edges for family rooms'])
        catalog.append(dict(id='product-{:04d}'.format(i), category=category,
            name='{} {} {} model {}'.format(color, material, category, i),
            reserve=rng.randrange(60, 501), description=(
                '{} {} {}. Height {} cm, width {} cm; {}. Standard-size accessory kit, '
                'replaceable fittings and illustrated assembly guide included.').format(color, material, category, height, width, features)))
    cases = [dict(id='C{}'.format(i + 1), query=query, expected_id=item['id'])
             for i, (query, item) in enumerate(zip(QUERIES, SPECIAL))]
    shops = []
    for i, (category, reserve) in enumerate(zip(CATEGORIES, RESERVES)):
        for premium in (False, True):
            tag = 'premium' if premium else 'value'
            shop_reserve = reserve + (70 if premium else 0)
            shops.append(dict(id='{}-{}'.format(category, tag), name='{} {} shop'.format(tag.title(), category),
                category=category, reserve=shop_reserve,
                guide=[shop_reserve, shop_reserve + 60],
                description=('Specialist in {}. {} products for home and office. Customers can discuss dimensions, '
                    'materials and purchase price before agreeing. An indicative price range is {} to {} credits; '
                    'this is guidance, not a binding offer.').format(category, tag, shop_reserve, shop_reserve + 60)))
    shopping = dict(items=['lamp', 'chair', 'bookshelf', 'desk'], budget=900, repeats=3)
    return {'catalog.json': catalog, 'challenge_cases.json': cases, 'shops.json': shops, 'shopping.json': shopping}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for name, data in build().items():
        with (args.output / name).open('x', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        print(name, len(data))
