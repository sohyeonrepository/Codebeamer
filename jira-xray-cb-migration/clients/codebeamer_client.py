"""No guessed write endpoints. Operations must match the supplied OpenAPI document."""
import re
import urllib.parse
from pathlib import Path
import json
import html
from clients.http import ApiError
from clients.jira_client import segment


class CodebeamerClient:
    def __init__(self, http, config, write=False):
        self.http, self.config, self.write = http, config, write
        self.spec = None

    def discover(self):
        location = self.config.get('swagger_url')
        if location:
            if location.startswith('file:'):
                try:self.spec = json.loads(Path(location[5:]).read_text(encoding='utf-8-sig'))
                except (OSError,ValueError) as exc:
                    raise ApiError('Swagger file could not be read as JSON: '+type(exc).__name__) from None
            else:
                self.spec,self.config['discovered_swagger_url'] = self._discover_from_urls([location])
        else:
            legacy=self._legacy_discover()
            if legacy:
                self.spec,self.config['discovered_swagger_url']=legacy
            else:
                self.spec,self.config['discovered_swagger_url'] = self._discover_from_urls(self._swagger_candidates())
        if not isinstance(self.spec,dict) or not isinstance(self.spec.get('paths'), dict):
            raise ApiError('Not an OpenAPI/Swagger document')
        if self.config.get('profile') == '3.0.0.7':
            from clients.codebeamer_3007 import bind
            bind(self)
        return [{'path': p, 'method': m.upper(), 'operationId': op.get('operationId')}
                for p, methods in self.spec['paths'].items() for m, op in methods.items()
                if m.lower() in ('get', 'post', 'put', 'patch', 'delete')]

    def _legacy_discover(self):
        """Preserve the exact discovery sequence used by the successful live-import build."""
        base=urllib.parse.urlsplit(self.http.base)
        parents=[base.path.rstrip('/'),base.path.rstrip('/').rsplit('/',1)[0]]
        for parent in dict.fromkeys(parents):
            for suffix in ('/v3/api-docs','/v2/api-docs','/swagger.json','/api-docs'):
                candidate=urllib.parse.urlunsplit((base.scheme,base.netloc,parent+suffix,'',''))
                try:spec=self.http.request('GET',candidate)
                except (ApiError,ValueError):continue
                if isinstance(spec,dict) and isinstance(spec.get('paths'),dict):return spec,candidate

    def _swagger_candidates(self):
        """Cover CB context roots such as /cb and REST bases such as /cb/api."""
        base=urllib.parse.urlsplit(self.http.base)
        path=base.path.rstrip('/')
        roots=[]
        for suffix in ('/api/v3','/api','/v3'):
            if path.endswith(suffix):roots.append(path[:-len(suffix)])
        roots.append(path)
        if '/' in path.rstrip('/'):
            roots.append(path.rsplit('/',1)[0])
        urls=[]
        for root in dict.fromkeys(roots):
            for suffix in ('/v3/swagger/api-docs','/v3/openapi.json','/v3/swagger.json','/v3/api-docs',
                           '/v3/swagger/editor.spr','/v2/api-docs','/swagger.json','/api-docs'):
                urls.append(urllib.parse.urlunsplit((base.scheme,base.netloc,root+suffix,'','')))
        return list(dict.fromkeys(urls))

    def _discover_from_urls(self, initial):
        queue=list(dict.fromkeys(initial));attempted=[];failures=[];seen=set()
        base=urllib.parse.urlsplit(self.http.base)
        while queue and len(seen)<40:
            candidate=queue.pop(0)
            if not urllib.parse.urlsplit(candidate).scheme:
                origin=urllib.parse.urlsplit(self.http.base)
                candidate=urllib.parse.urlunsplit((origin.scheme,origin.netloc,
                    candidate if candidate.startswith('/') else origin.path.rstrip('/')+'/'+candidate,'',''))
            if candidate in seen:continue
            seen.add(candidate);attempted.append(urllib.parse.urlsplit(candidate).path)
            try:
                content=self.http.request('GET',candidate,raw=True)
            except ApiError as exc:
                failures.append(urllib.parse.urlsplit(candidate).path+': '+str(exc));continue
            if isinstance(content,dict):document=content
            else:
                try:
                    text=content.decode('utf-8-sig') if isinstance(content,(bytes,bytearray)) else str(content)
                except UnicodeDecodeError:
                    failures.append(urllib.parse.urlsplit(candidate).path+': response is neither JSON nor UTF-8 HTML');continue
                try:document=json.loads(text)
                except ValueError:document=self._embedded_spec(text)
                if not isinstance(document,dict):
                    links=self._swagger_links(text,candidate,base)
                    if links:
                        queue.extend(link for link in links if link not in seen)
                    else:
                        lower=text.casefold()
                        reason=('HTML appears to be a login page; the supplied Basic credentials did not authorize Swagger UI'
                                if '<form' in lower and ('password' in lower or 'login' in lower or 'sign in' in lower)
                                else 'HTML did not expose an OpenAPI JSON URL or initializer script')
                        failures.append(urllib.parse.urlsplit(candidate).path+': '+reason)
                    continue
            if isinstance(document.get('paths'),dict):return document,candidate
            failures.append(urllib.parse.urlsplit(candidate).path+': JSON has no paths object')
        important=[f for f in failures if 'editor.spr' in f or 'HTTP 401' in f or 'HTTP 403' in f or 'HTML' in f]
        summary=list(dict.fromkeys(important[-6:]+failures[-4:]))
        tail='; '.join(summary) if summary else 'no readable response'
        raise ApiError('Swagger discovery failed. Tried '+', '.join(attempted)+'; '+tail+
                       '; no target write attempted. The Swagger UI may be used as input only when it exposes a same-server JSON definition.')

    @staticmethod
    def _embedded_spec(text):
        decoder=json.JSONDecoder()
        for content in (text,html.unescape(text)):
            for marker in ('{"openapi"','{"swagger"'):
                start=0
                while True:
                    index=content.find(marker,start)
                    if index<0:break
                    try:
                        value,_=decoder.raw_decode(content[index:])
                        if isinstance(value,dict) and isinstance(value.get('paths'),dict):return value
                    except ValueError:pass
                    start=index+1

    @staticmethod
    def _swagger_links(text,page,base):
        values=[]
        decoded=html.unescape(text)
        patterns=(r'\burl\s*[:=]\s*["\']([^"\'<>\s]+)["\']',
                  r'["\']([^"\'<>\s]*(?:openapi|swagger|api-docs)[^"\'<>\s]*)["\']')
        for match in (match for pattern in patterns for match in re.finditer(pattern,decoded,re.I)):
            value=match.group(1).replace('\\/','/')
            if value.lower().endswith(('.css','.png','.svg','.ico','.map')) or 'editor.spr' in value.lower():continue
            url=urllib.parse.urljoin(page,value)
            parsed=urllib.parse.urlsplit(url)
            if (parsed.scheme,parsed.netloc)==(base.scheme,base.netloc) and not parsed.username and not parsed.password:
                values.append(url)
        return list(dict.fromkeys(values))

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
        operation_name = name
        op = self.operation(name)
        if name == 'trackers_get' and op.get('aggregate_types') and 'typeId' not in (params or {}):
            from clients.codebeamer_3007 import typed_project_trackers
            return typed_project_trackers(self, params or {})
        method = op['method'].upper()
        if method == 'DELETE':
            raise ApiError('Migration never deletes existing target data')
        if method != 'GET' and not self.write:
            raise ApiError('Write refused in dry-run')
        context = dict(params or {}, payload=data)
        path_params = dict(params or {})
        # Bind a generic {id} only for verified project-scoped read operations.
        if method == 'GET' and operation_name == 'project_get' and re.fullmatch(r'(?:/v3)?/projects/\{id\}/?',op['path']):
            if 'id' not in path_params and 'projectId' in path_params:
                path_params['id'] = path_params['projectId']
        if method == 'GET' and operation_name == 'trackers_get' and re.search(r'/projects/\{id\}/trackers(?:/|$)',op['path']):
            if 'id' not in path_params and 'projectId' in path_params:
                path_params['id'] = path_params['projectId']
        for name, template in op.get('path_parameters', {}).items():
            path_params[name] = render(template, context)
        try:
            path = re.sub(r'\{([^}]+)\}', lambda m: segment(path_params[m[1]]), op['path'])
        except KeyError as exc:
            raise ApiError('Missing path parameter for ' + operation_name + ': ' + str(exc.args[0]) + '; path: ' + op['path']) from None
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
            if self.config.get('profile') == '3.0.0.7':
                from mapping_helper import request_schema
                from single_import import validate
                validate(self.spec,data,request_schema(self.spec,self.spec['paths'][op['path']][method.lower()]))
        result = self.http.request(method, path, data, raw=raw, headers=headers)
        if self.config.get('profile') == '3.0.0.7' and op['path'] == '/v3/tracker/configuration' and method == 'POST' and not raw:
            if isinstance(result,dict) and isinstance(result.get('basicInformation'),dict) and type(result['basicInformation'].get('trackerId')) is int:
                result = dict(result,id=result['basicInformation']['trackerId'])
            elif type(result) is int:
                result = {'id':result}
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
        if 'fields' in self.config.get('operations', {}):
            return self.call('fields', {'trackerId': tracker})
        return self.http.request('GET', f'/v3/trackers/{segment(tracker)}/fields')

    def configuration(self, tracker):
        if 'tracker_configuration_get' in self.config.get('operations', {}):
            return self.call('tracker_configuration_get', {'trackerId': tracker})
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
