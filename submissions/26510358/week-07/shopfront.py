"""Public merchandising from actual catalogue facts and existing pricing rules."""
from pricing import opening_ask

BASE_IDS = ('lamp', 'chair', 'bookshelf')


def public_items(catalog, quote_policy):
    indexed = {item['id']: item for item in catalog}
    offers = []
    for item_id in BASE_IDS:
        if item_id not in indexed:
            continue
        item = indexed[item_id]
        price = opening_ask(item, quote_policy)
        attrs = item['attributes']
        features = []
        if item_id == 'lamp':
            if 'warm LED' in item['description']:
                features.append('warm LED')
            if 'adjustable shade' in item['description']:
                features.append('adjustable shade')
        if attrs.get('wood_species'):
            features.append(str(attrs['wood_species']) + ' wood')
        if attrs.get('shelves_count'):
            features.append(str(attrs['shelves_count']) + ' shelves')
        if attrs.get('capacity_books'):
            features.append('about ' + str(attrs['capacity_books']) + ' books')
        if attrs.get('height_cm'):
            features.append(str(attrs['height_cm']) + ' cm high')
        # Only public quotes and real attributes leave the pricing boundary.
        # Catalogue names contain an English title followed by a Korean alias.
        offer = {'id': item_id, 'name': item['name'].partition(' / ')[0], 'price': price, 'features': features,
                 'attributes': dict(attrs)}
        offers.append(offer)
    return offers


def price_line(offers):
    return ', '.join(f"{item['id']} {item['price']}" for item in offers)


def public_description(offers):
    return ('Ssadassa Shop: ' + price_line(offers) + ' credits each. '
            'Clear quotes, prompt acceptance and Ed25519-signed receipts matching the agreed item and price. '
            'Request a current quote, then offer that total on the same task. '
            '(Catalog search and price negotiation)')


def public_skills(offers):
    return [{'id': 'negotiate', 'name': 'Catalog search and price negotiation',
             'description': 'Request a current single-unit quote and offer that total on the same task.',
             'tags': ['shopping', 'catalog', 'negotiation']}] + [
            {'id': 'buy-' + item['id'], 'name': item['name'],
             'description': (f"Latest base quote: {item['price']} credits. " + ', '.join(item['features']) +
                             '. Request a current quote, then offer that number on the same task for a signed receipt.'),
             'tags': ['shopping', 'furniture', item['id']],
             'examples': ['I want to buy a ' + item['id'], str(item['price'])]} for item in offers]
