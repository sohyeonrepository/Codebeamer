"""Verified TLS, same-origin credentials, bounded retries, metadata-only audit log."""
import hashlib
import base64
import getpass
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class ApiError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        fp.close()
        raise ApiError('Redirect refused; configure the canonical API URL')


class Http:
    def __init__(self, base, token_env=None, log=None, timeout=60, *, auth="bearer",
                 username_env=None, password_env=None, allow_http=False, prompt_auth=False):
        self.base = base.rstrip('/')
        self.allow_http = allow_http is True
        self.authorization = None
        if auth == 'basic':
            username = os.environ.get(username_env, '') if username_env else ''
            password = os.environ.get(password_env, '') if password_env else ''
            if prompt_auth:
                username = username or input('Codebeamer username: ')
                password = password or getpass.getpass('Codebeamer password: ')
            if not username or not password or ':' in username:
                raise ApiError('Basic authentication requires a username (without colon) and password; use --prompt-auth or configured environment variables')
            encoded = base64.b64encode((username + ':' + password).encode('utf-8')).decode('ascii')
            self.authorization = 'Basic ' + encoded
        elif auth == 'bearer':
            token = os.environ.get(token_env, '') if token_env else ''
            if not token and prompt_auth:
                token = getpass.getpass((token_env or 'API') + ' token: ')
            if token:
                self.authorization = 'Bearer ' + token
        elif auth != 'none':
            raise ApiError('Unsupported authentication type')
        self.log = Path(log) if log else None
        self.timeout = timeout
        self.opener = urllib.request.build_opener(NoRedirect())

    def url(self, path):
        url = path if path.startswith(('https://', 'http://')) else self.base + '/' + path.lstrip('/')
        p, b = urllib.parse.urlsplit(url), urllib.parse.urlsplit(self.base)
        if (p.scheme, p.netloc) != (b.scheme, b.netloc) or p.username or p.password:
            raise ApiError('Cross-origin URL or embedded credentials refused')
        if p.scheme not in ('http', 'https'):
            raise ApiError('HTTP(S) URL required')
        if p.scheme == 'http' and p.hostname not in ('127.0.0.1', 'localhost', '::1') and not self.allow_http:
            raise ApiError('HTTPS required; set allow_http=true only for a trusted internal HTTP server')
        return url

    def request(self, method, path, data=None, raw=False, headers=None):
        url = self.url(path)
        body = json.dumps(data, ensure_ascii=False).encode() if data is not None and not isinstance(data, bytes) else data
        h = {'Accept': 'application/json'}
        if self.authorization:
            h['Authorization'] = self.authorization
        if body is not None:
            h['Content-Type'] = 'application/json'
        h.update(headers or {})
        # Writes are never automatically retried: an uncertain write must be reconciled.
        for attempt in range(3 if method == 'GET' else 1):
            start = time.monotonic()
            status, content = None, b''
            try:
                with self.opener.open(urllib.request.Request(url, data=body, headers=h, method=method), timeout=self.timeout) as response:
                    status = response.status
                    content = response.read()
                return content if raw else (json.loads(content) if content else {})
            except urllib.error.HTTPError as exc:
                status = exc.code
                exc.close()
                if method == 'GET' and status in (429, 502, 503, 504) and attempt < 2:
                    time.sleep(min(2 ** attempt, 4))
                    continue
                raise ApiError(f'{method} request failed: HTTP {status}') from None
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise ApiError(f'{method} transport failure ({type(exc).__name__}); write outcome may be unknown') from None
            finally:
                if self.log:
                    self.log.parent.mkdir(parents=True, exist_ok=True)
                    # No URL query, headers, bodies or server error text: these may contain secrets.
                    entry = {'method': method, 'path_hash': hashlib.sha256(urllib.parse.urlsplit(url).path.encode()).hexdigest(),
                             'status': status, 'request_bytes': len(body or b''), 'response_bytes': len(content),
                             'elapsed_ms': round((time.monotonic() - start) * 1000)}
                    with self.log.open('a', encoding='utf-8') as f:
                        f.write(json.dumps(entry) + '\n')
