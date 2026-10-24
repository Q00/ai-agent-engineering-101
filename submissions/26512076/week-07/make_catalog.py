"""Generate a reproducible 3,000-item catalog. No API calls."""
import json
from pathlib import Path


def make_catalog():
    rows = []
    materials = ['wood', 'metal', 'plastic', 'bamboo', 'glass']
    for i in range(1000):
        m = materials[i % 5]
        height = [55, 70, 90, 120, 160, 190][(i // 5) % 6]
        capacity = [20, 40, 60, 80][(i // 30) % 4]
        rows.append({'sku': f'B{i:04}', 'name': f'{m.title()} bookshelf {i:04}',
            'description': f'A {m} bookshelf for storing about {capacity} books, {height} cm tall.',
            'category': 'bookshelf', 'material': m, 'height_cm': height,
            'capacity_books': capacity, 'reserve': 100 + (i % 31) * 7})
        height = [30, 40, 50, 60][(i // 5) % 4]
        dimmable = (i // 20) % 2 == 0
        rows.append({'sku': f'L{i:04}', 'name': f'{m.title()} desk lamp {i:04}',
            'description': f'A {m} desk lamp, {height} cm tall, ' + ('with adjustable brightness (dimmable).' if dimmable else 'with fixed brightness.'),
            'category': 'lamp', 'material': m, 'height_cm': height,
            'dimmable': dimmable, 'reserve': 50 + (i % 29) * 5})
        ergonomic = (i // 5) % 2 == 0
        adjustable = (i // 10) % 2 == 0
        height = [80, 90, 100, 110][(i // 20) % 4]
        rows.append({'sku': f'C{i:04}', 'name': f'{m.title()} office chair {i:04}',
            'description': f'A {m} office chair, {height} cm tall, ' + ('ergonomic back support' if ergonomic else 'a standard back') + ', ' + ('adjustable seat height.' if adjustable else 'fixed seat height.'),
            'category': 'chair', 'material': m, 'height_cm': height,
            'ergonomic': ergonomic, 'adjustable': adjustable, 'reserve': 90 + (i % 37) * 6})
    return rows


if __name__ == '__main__':
    path = Path(__file__).with_name('catalog.json')
    path.write_text(json.dumps(make_catalog(), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Created {len(make_catalog())} items: {path}')
