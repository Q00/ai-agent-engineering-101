"""Rank stock promotion by observed demand and obtainable absolute margin."""
def promotion_values(inventory,market):
    values={}
    for unit in inventory.get('units',[]):
        sku=unit['sku'];flow=market.get('skus',{}).get(sku,{})
        quotes=flow.get('prices',{}).get(str(unit.get('grade')),{} )
        sale=quotes.get('base_median',quotes.get('median',unit.get('list_price',0)))
        rate=max(flow.get('own_rate',0),flow.get('market_rate',0)*.35)
        value=max(0,sale-unit.get('cost',0))*rate
        values[sku]=max(values.get(sku,0),value)
    return values
