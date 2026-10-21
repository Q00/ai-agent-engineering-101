"""Public merchandising from actual catalogue facts and existing pricing rules."""
from rank_pricing import opening_ask
from referral_pricing import referral_terms

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
        terms = referral_terms(item, price, quote_policy, 'public_code_preview')
        # Only public quotes and real attributes leave the pricing boundary.
        offer = {'id': item_id, 'name': item['name'], 'price': price, 'features': features,
                 'attributes': dict(attrs)}
        if terms.get('applied'):
            offer['discount'] = {key: terms[key] for key in ('code', 'percent', 'price')}
        offers.append(offer)
    return offers


def price_line(offers):
    return ', '.join(f"{item['id']} {item['price']}" for item in offers)


def public_description(offers):
    text = ('Ssadassa Shop by myeonggi. Latest base quotes: ' + price_line(offers) + ' credits. '
            'Request a current quote, then send that number on the same task for an '
            'Ed25519-signed receipt at the exact agreed price. ')
    text += '; '.join(item['id'] + ': ' + ', '.join(item['features']) for item in offers) + '. '
    discounts = [item for item in offers if item.get('discount')]
    if discounts:
        text += 'REVIEW10: 10% off eligible standard quotes; item + code in the first message. '
    return text + 'Quotes can change between orders; no purchase or rating promise is needed to ask.'


def public_skills(offers):
    return [{'id': 'buy-' + item['id'], 'name': item['name'],
             'description': (f"Latest base quote: {item['price']} credits. " + ', '.join(item['features']) +
                             '. Request a current quote, then offer that number on the same task for a signed receipt.'),
             'tags': ['shopping', 'furniture', item['id']],
             'examples': ['I want to buy a ' + item['id'], str(item['price'])]} for item in offers]
