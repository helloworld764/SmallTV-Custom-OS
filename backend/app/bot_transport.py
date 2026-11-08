"""Small Telegram Bot API client. Exceptions never contain token-bearing URLs."""

import json
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class TelegramError(Exception):
    def __init__(self, status=0, retry_after=5):
        super().__init__('Telegram request failed')
        self.status = status if type(status) is int else 0
        self.retry_after = max(1, min(retry_after, 300)) if type(retry_after) is int else 5


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class TelegramClient:
    def __init__(self, token):
        self._token = token
        self._opener = build_opener(NoRedirect())

    def call(self, method, payload=None):
        if method not in ('getMe', 'getWebhookInfo', 'getUpdates', 'sendMessage', 'answerCallbackQuery'):
            raise ValueError('Unsupported Telegram method')
        request = Request(f'https://api.telegram.org/bot{self._token}/{method}',
                          data=json.dumps(payload or {}).encode('utf-8'),
                          headers={'Content-Type': 'application/json'}, method='POST')
        try:
            with self._opener.open(request, timeout=35) as response:
                raw = response.read(1048577)
            if len(raw) > 1048576:
                raise ValueError('Oversized Telegram response')
            result = json.loads(raw)
            if not isinstance(result, dict) or result.get('ok') is not True:
                raise TelegramError()
            return result['result']
        except HTTPError as error:
            delay = 5
            try:
                body = json.loads(error.read(16384))
                delay = body.get('parameters', {}).get('retry_after', 5)
            except (ValueError, TypeError, AttributeError):
                pass
            raise TelegramError(error.code, delay) from None
        except TelegramError:
            raise
        except Exception:
            # Includes network, decoding and schema errors; no raw exception escapes.
            raise TelegramError() from None
