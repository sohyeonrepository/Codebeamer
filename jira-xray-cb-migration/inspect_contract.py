"""Print schema contracts from a previously collected API definition; no network/auth."""
import argparse
import json
from pathlib import Path


def local_ref(spec, value):
    if not isinstance(value, dict) or '$ref' not in value:
        return value
    ref = value['$ref']
    if not ref.startswith('#/'):
        return value
    node = spec
    for key in ref[2:].split('/'):
        node = node[key.replace('~1', '/').replace('~0', '~')]
    return node


def summarize(spec, schema, depth):
    if not isinstance(schema, dict):
        return schema
    reference = schema.get('$ref')
    result = {'schema': reference.rsplit('/', 1)[-1]} if reference else {}
    if depth < 0:
        return result or {'type': schema.get('type', 'object')}
    schema = local_ref(spec, schema)
    for key in ('type', 'format', 'required', 'enum', 'discriminator', 'nullable'):
        if key in schema:
            result[key] = schema[key]
    if 'properties' in schema:
        result['properties'] = {key: summarize(spec, value, depth - 1) for key, value in schema['properties'].items()}
    if 'items' in schema:
        result['items'] = summarize(spec, schema['items'], depth - 1)
    for key in ('oneOf', 'allOf', 'anyOf'):
        if key in schema:
            result[key] = [summarize(spec, value, depth - 1) for value in schema[key]]
    return result


def contracts(capabilities, selected, depth):
    spec = capabilities['openapi']
    output = []
    for path, methods in spec['paths'].items():
        for method, operation in methods.items():
            if not isinstance(operation, dict) or operation.get('operationId') not in selected:
                continue
            schemas = []
            if 'requestBody' in operation:
                body = local_ref(spec, operation['requestBody'])
                schemas.extend({'media_type': media, 'schema': summarize(spec, content.get('schema', {}), depth)}
                               for media, content in body.get('content', {}).items())
            for parameter in operation.get('parameters', []):
                parameter = local_ref(spec, parameter)
                if parameter.get('in') == 'body':
                    schemas.append({'schema': summarize(spec, parameter.get('schema', {}), depth)})
            output.append({'operationId': operation['operationId'], 'method': method.upper(), 'path': path, 'request': schemas})
    missing = sorted(set(selected) - {op['operationId'] for op in output})
    return {'operations': output, 'missing': missing}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capabilities', default='.migration/run-api-capabilities.json')
    parser.add_argument('--operation', action='append')
    parser.add_argument('--depth', type=int, default=2)
    args = parser.parse_args()
    selected = args.operation or ['createTrackerItem', 'updateCustomFieldTrackerItem', 'updateTestRunResult', 'postTrackerConfiguration']
    try:
        data = json.loads(Path(args.capabilities).read_text(encoding='utf-8-sig'))
        print(json.dumps(contracts(data, selected, max(0, min(args.depth, 6))), ensure_ascii=False, indent=2))
    except (OSError, ValueError, KeyError):
        parser.exit(2, 'Cannot read capabilities/API schema; check --capabilities path and file format.\n')


if __name__ == '__main__':
    main()
