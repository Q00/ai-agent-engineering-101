"""Generate 20 reproducible shop catalogs and a shopping list; no model calls."""
import json
import math
from pathlib import Path
ROOT = Path(__file__).resolve().parent


def generate(root=ROOT):
    (root / 'shop_catalogs').mkdir(exist_ok=True)
    shops = []
    for i in range(20):
        item = ['lamp', 'chair', 'bookshelf'][i % 3]
        reserve = {'lamp': 100, 'chair': 180, 'bookshelf': 260}[item] + (i // 3) % 5 * 3
        sku = f'S{i:02}-{item}'
        product = {'sku': sku, 'name': f'{item} from Shop {i:02}',
            'description': f'A practical {item} for a home office.',
            'category': item, 'reserve': reserve}
        name = f'shop_catalogs/shop_{i:02}.json'
        (root / name).write_text(json.dumps([product], indent=2) + '\n', encoding='utf-8')
        shops.append({'shop_id': f'shop_{i:02}', 'name': f'Office {item.title()} Shop {i:02}',
            'catalog': name, 'skill_description': f'Specialist shop selling {item}. Public price guide: {math.ceil(reserve * 1.3)}. Ask for a {item} and negotiate using numeric offers.'})
    (root / 'shops.json').write_text(json.dumps(shops, indent=2) + '\n', encoding='utf-8')
    shopping = {'total_budget': 800, 'items': [
        {'item_id': 'desk_light', 'item': 'lamp', 'description': 'One lamp for my desk'},
        {'item_id': 'office_seat', 'item': 'chair', 'description': 'One chair for my home office'},
        {'item_id': 'book_storage', 'item': 'bookshelf', 'description': 'One bookshelf for my books'}]}
    (root / 'shopping_list.json').write_text(json.dumps(shopping, indent=2) + '\n', encoding='utf-8')
    return shops


if __name__ == '__main__':
    print(f'Generated {len(generate())} shops and shopping_list.json')
