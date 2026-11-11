"""Small Telegram Bot API client. Exceptions never contain token-bearing URLs."""

import json
from uuid import uuid4
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
        if method not in ('getMe', 'getWebhookInfo', 'getUpdates', 'sendMessage', 'editMessageText', 'answerCallbackQuery'):
            raise ValueError('Unsupported Telegram method')
        return self._request(method,json.dumps(payload or {}).encode('utf-8'),'application/json')

    def send_photo(self, payload, path):
        try:
            with path.open('rb') as file:
                photo = file.read(1048577)
            if len(photo) > 1048576 or not photo.startswith(b'\x89PNG\r\n\x1a\n'):
                raise ValueError('Invalid catalogue photo')
            boundary = 'storm'+uuid4().hex
            body = bytearray()
            for key in ('chat_id','caption','reply_markup'):
                value = json.dumps(payload[key],ensure_ascii=False) if key == 'reply_markup' else str(payload[key])
                body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode('utf-8'))
            body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="pet.png"\r\nContent-Type: image/png\r\n\r\n'.encode())
            body.extend(photo)
            body.extend(f'\r\n--{boundary}--\r\n'.encode())
        except Exception:
            raise TelegramError(400) from None
        return self._request('sendPhoto',bytes(body),'multipart/form-data; boundary='+boundary)

    def _request(self, method, data, content_type):
        request = Request(f'https://api.telegram.org/bot{self._token}/{method}',
                          data=data, headers={'Content-Type': content_type}, method='POST')
        try:
            with self._opener.open(request, timeout=3 if method=='answerCallbackQuery' else 35) as response:
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
