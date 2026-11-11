"""Small public price cache. No keys, user input URLs or fabricated fallback rates."""
import json
import math
import time
from threading import Lock
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from fastapi import APIRouter, Depends
from .auth import current_user

router = APIRouter(prefix='/api/v1', tags=['markets'])
SOURCES = {
    'crypto': 'https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,the-open-network&vs_currencies=usd&include_last_updated_at=true',
    'fiat': 'https://www.cbr.ru/scripts/XML_daily.asp',
}

def fetch_source(kind):
    request = Request(SOURCES[kind], headers={'User-Agent': 'StormOS/0.1 price display'})
    with urlopen(request, timeout=3) as response:
        payload = response.read(131073)
    if len(payload) > 131072:
        raise ValueError('Price response too large')
    return payload

def positive(value):
    if isinstance(value, bool): raise ValueError('Invalid price')
    value = float(value)
    if not math.isfinite(value) or value <= 0: raise ValueError('Invalid price')
    return value

def parse_source(kind, payload):
    if kind == 'crypto':
        data = json.loads(payload)
        return [{'symbol': symbol, 'price': positive(data[key]['usd']), 'unit': 'USD',
                 'source_time': int(data[key]['last_updated_at'])}
                for symbol, key in [('BTC','bitcoin'),('ETH','ethereum'),('TON','the-open-network')]]
    if b'<!DOCTYPE' in payload.upper() or b'<!ENTITY' in payload.upper():
        raise ValueError('XML entities are not allowed')
    root = ElementTree.fromstring(payload)
    rows = {row.findtext('CharCode'): row for row in root.findall('Valute')}
    return [{'symbol': symbol, 'price': positive(rows[symbol].findtext('Value').replace(',','.')) /
             positive(rows[symbol].findtext('Nominal')), 'unit': 'RUB', 'source_date': root.attrib['Date']}
            for symbol in ('USD','EUR')]

class PriceCache:
    def __init__(self):
        self.lock = Lock()
        self.groups = {}
        self.attempts = {}

    def read(self, fetcher=fetch_source, now=None):
        now = time.time() if now is None else now
        with self.lock:
            result = []
            for kind, symbols in [('crypto', ('BTC','ETH','TON')), ('fiat', ('USD','EUR'))]:
                ttl = 300 if kind == 'crypto' else 3600
                if now - self.attempts.get(kind, float('-inf')) >= ttl:
                    self.attempts[kind] = now
                    try:
                        rows = parse_source(kind, fetcher(kind))
                        self.groups[kind] = (now, rows)
                    except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError, ElementTree.ParseError):
                        pass
                fetched, rows = self.groups.get(kind, (None, []))
                usable = fetched is not None and now - fetched <= (3600 if kind == 'crypto' else 259200)
                for symbol in symbols:
                    row = next((r for r in rows if r['symbol'] == symbol), {}) if usable else {}
                    stale = fetched is None or now - fetched >= ttl
                    if kind == 'crypto' and row:
                        age = now - row['source_time']
                        if age < -300 or age > 3600: row = {}
                        stale = stale or age > 600
                    result.append({'symbol': symbol, 'price': None, 'unit': 'USD' if kind == 'crypto' else 'RUB',
                                   **row, 'stale': stale, 'fetched_at': fetched})
            return {'rows': result, 'server_time': now, 'sources': ['CoinGecko', 'Банк России']}

cache = PriceCache()

@router.get('/markets', dependencies=[Depends(current_user)])
def markets():
    return cache.read()
