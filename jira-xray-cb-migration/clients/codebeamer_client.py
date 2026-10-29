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
        if location:
            self.spec = json.loads(Path(location[5:]).read_text(encoding='utf-8')) if location.startswith('file:') else self.http.request('GET', location)
        else:
            # Read-only probes, never assume a candidate is an actual API definition.
            base = urllib.parse.urlsplit(self.http.base)
            parents = [base.path.rstrip('/'), base.path.rstrip('/').rsplit('/', 1)[0]]
            attempted = []
            for parent in dict.fromkeys(parents):
                for suffix in ('/v3/api-docs', '/v2/api-docs', '/swagger.json', '/api-docs'):
                    candidate = urllib.parse.urlunsplit((base.scheme, base.netloc, parent + suffix, '', ''))
                    try:
                        spec = self.http.request('GET', candidate)
                        if isinstance(spec, dict) and isinstance(spec.get('paths'), dict):
                            self.spec = spec
                            self.config['discovered_swagger_url'] = candidate
                            break
                    except (ApiError, ValueError):
                        pass
                    attempted.append(parent + suffix)
                if self.spec is not None:
                    break
            if self.spec is None:
                raise ApiError('Swagger JSON discovery failed at ' + ', '.join(attempted) + '; no target write attempted. Supply codebeamer.swagger_url or a file: JSON definition')
        if not isinstance(self.spec.get('paths'), dict):
            raise ApiError('Not an OpenAPI/Swagger document')
        return [{'path': p, 'method': m.upper(), 'operationId': op.get('operationId')}
                for p, methods in self.spec['paths'].items() for m, op in methods.items()
                if m.lower() in ('get', 'post', 'put', 'patch', 'delete')]

    def operation(self, name):
        op = self.config.get('operations', {}).get(name)
        if not op:
            raise ApiError('Unconfigured API capability: ' + name)
        if self.spec is None:
            raise ApiError('Discover Swagger before using API operations')
        if 'operationId' in op and 'path' not in op:
            matches = [(p, m) for p, methods in self.spec['paths'].items() for m, definition in methods.items()
                       if isinstance(definition, dict) and definition.get('operationId') == op['operationId']]
            if len(matches) != 1:
                raise ApiError('Ambiguous/missing Swagger operationId: ' + op['operationId'])
            op = dict(op, path=matches[0][0], method=matches[0][1].upper())
        path, method = op['path'], op['method'].lower()
        if self.spec is None or method not in self.spec['paths'].get(path, {}):
            raise ApiError('Operation not present in discovered Swagger: ' + name)
        if (name == 'history' or name.endswith('_history')) and method != 'get':
            raise ApiError('History is read-only')
        if (name.endswith(('_get', '_lookup')) or name in ('history', 'fields', 'lookup')) and method != 'get':
            raise ApiError('Read operation must use GET: ' + name)
        return op

    def call(self, name, params=None, data=None, raw=False, headers=None):
        op = self.operation(name)
        method = op['method'].upper()
        if method != 'GET' and not self.write:
            raise ApiError('Write refused in dry-run')
        context = dict(params or {}, payload=data)
        path_params = dict(params or {})
        for name, template in op.get('path_parameters', {}).items():
            path_params[name] = render(template, context)
        try:
            path = re.sub(r'\{([^}]+)\}', lambda m: segment(path_params[m[1]]), op['path'])
        except KeyError:
            raise ApiError('Missing path parameter for configured operation') from None
        if op.get('query'):
            query = {k: render(v, context) for k, v in op['query'].items()}
            path += '?' + urllib.parse.urlencode(query, doseq=True)
        if 'body' in op and not isinstance(data, bytes):
            data = render(op['body'], context)
        bindings = op.get('field_bindings', self.config.get('field_bindings', {}))
        if method in ('POST', 'PUT', 'PATCH') and not isinstance(data, bytes):
            from clients.codebeamer_payloads import request_body
            operation_id = self.spec['paths'][op['path']][method.lower()].get('operationId')
            data = request_body(operation_id, data, bindings)
        result = self.http.request(method, path, data, raw=raw, headers=headers)
        for part in op.get('response_path', '').split('.'):
            if part:
                result = result[part]
        if op.get('response_format') == 'field-values':
            from clients.codebeamer_payloads import canonical_fields
            result = canonical_fields(result, bindings)
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
