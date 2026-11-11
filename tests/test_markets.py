import json
import pytest
from backend.app.markets import PriceCache, parse_source

def source(kind):
    if kind == 'crypto':
        return json.dumps({key: {'usd': price, 'last_updated_at': 1000} for key, price in
                           [('bitcoin', 100), ('ethereum', 20), ('the-open-network', 3)]}).encode()
    return b'<ValCurs Date="11.11.2026"><Valute><CharCode>USD</CharCode><Nominal>1</Nominal><Value>80,10</Value></Valute><Valute><CharCode>EUR</CharCode><Nominal>10</Nominal><Value>900,00</Value></Valute></ValCurs>'

def test_rates_units_order_and_nominal():
    rows = PriceCache().read(source, 1000)['rows']
    assert [r['symbol'] for r in rows] == ['BTC','ETH','TON','USD','EUR']
    assert rows[-1]['price'] == 90 and rows[-1]['unit'] == 'RUB'
    assert all(not r['stale'] for r in rows)

def test_cache_backoff_partial_failure_and_expiration():
    cache = PriceCache(); calls = []
    def fetch(kind): calls.append(kind); return source(kind)
    cache.read(fetch, 1000); cache.read(fetch, 1001)
    assert calls == ['crypto','fiat']
    def failed(kind): raise OSError('offline')
    stale = cache.read(failed, 1400)['rows']
    assert stale[0]['stale'] and stale[0]['price'] == 100
    assert cache.read(failed, 5000)['rows'][0]['price'] is None
    assert all(r['price'] is None for r in PriceCache().read(failed, 1000)['rows'])

@pytest.mark.parametrize('price', [0, -1, True, float('nan'), float('inf')])
def test_invalid_prices_are_rejected(price):
    data = json.loads(source('crypto'))
    data['bitcoin']['usd'] = price
    with pytest.raises(ValueError): parse_source('crypto', json.dumps(data).encode())

def test_xml_entities_rejected():
    with pytest.raises(ValueError): parse_source('fiat', b'<!DOCTYPE x><ValCurs/>')
