"""No guessed write endpoints. Operations must match the supplied OpenAPI document."""
import re
import urllib.parse
from pathlib import Path
import json
from clients.http import ApiError
from clients.jira_client import segment


class CodebeamerClient:
    def __init__(self, http, config, write=False):
        self.http, self.config, self.write = http, config, write
        self.spec = None

    def discover(self):
        location = self.config.get('swagger_url')
        if not location:
            raise ApiError('Set codebeamer.swagger_url to the actual Swagger/OpenAPI JSON endpoint')
        self.spec = json.loads(Path(location[5:]).read_text(encoding='utf-8')) if location.startswith('file:') else self.http.request('GET', location)
        if not isinstance(self.spec.get('paths'), dict):
            raise ApiError('Not an OpenAPI/Swagger document')
        return [{'path': p, 'method': m.upper(), 'operationId': op.get('operationId')}
                for p, methods in self.spec['paths'].items() for m, op in methods.items()
                if m.lower() in ('get', 'post', 'put', 'patch', 'delete')]

    def operation(self, name):
        op = self.config.get('operations', {}).get(name)
        if not op:
            raise ApiError('Unconfigured API capability: ' + name)
        path, method = op['path'], op['method'].lower()
        if self.spec is None or method not in self.spec['paths'].get(path, {}):
            raise ApiError('Operation not present in discovered Swagger: ' + name)
        if name == 'history' and method != 'get':
            raise ApiError('History is read-only')
        if (name.endswith(('_get', '_lookup')) or name in ('history', 'fields', 'lookup')) and method != 'get':
            raise ApiError('Read operation must use GET: ' + name)
        return op

    def call(self, name, params=None, data=None, raw=False, headers=None):
        op = self.operation(name)
        method = op['method'].upper()
        if method != 'GET' and not self.write:
            raise ApiError('Write refused in dry-run')
        path = re.sub(r'\{([^}]+)\}', lambda m: segment((params or {})[m[1]]), op['path'])
        context = dict(params or {}, payload=data)
        if op.get('query'):
            query = {k: render(v, context) for k, v in op['query'].items()}
            path += '?' + urllib.parse.urlencode(query, doseq=True)
        if 'body' in op and not isinstance(data, bytes):
            data = render(op['body'], context)
        result = self.http.request(method, path, data, raw=raw, headers=headers)
        for part in op.get('response_path', '').split('.'):
            if part:
                result = result[part]
        if op.get('response_fields'):
            result = {target: resolve(result, source) for target, source in op['response_fields'].items()}
        return result

    def fields(self, tracker):
        return self.http.request('GET', f'/v3/trackers/{segment(tracker)}/fields')

    def configuration(self, tracker):
        return self.http.request('GET', f'/v3/tracker/{segment(tracker)}/configuration')


def resolve(document, path):
    for part in path.split('.'):
        if part:
            document = document[int(part)] if isinstance(document, list) else document[part]
    return document


def render(template, context):
    if isinstance(template, dict):
        return {k: render(v, context) for k, v in template.items()}
    if isinstance(template, list):
        return [render(v, context) for v in template]
    if isinstance(template, str) and template.startswith('${') and template.endswith('}'):
        return resolve(context, template[2:-1])
    return template
