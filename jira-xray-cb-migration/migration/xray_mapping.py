"""Plan-only mapping editor for verified native Run/Step request properties."""
from copy import deepcopy
from clients.http import ApiError
from mapping_helper import request_schema, schema_object
from migration.fields import convert

CATEGORIES = ('run', 'run_step', 'test_step')
CONVERTERS = ('text','number','boolean','date','datetime','user','option','status','reference','json','raw')


def writable_properties(cb, category):
    if category not in CATEGORIES:
        raise ApiError('Unsupported Xray record category')
    if not getattr(cb, 'spec', None):
        raise ApiError('Xray 대상 쓰기 스키마 미확인: 원본 조회와 별도로 실제 OpenAPI 계약이 필요합니다.')
    operation = cb.operation(category + '_create')
    if operation.get('method', '').upper() != 'POST':
        raise ApiError('Xray create operation must be a verified POST contract')
    try:
        schema = schema_object(cb.spec, request_schema(cb.spec, cb.spec['paths'][operation['path']]['post']))
    except (KeyError, TypeError) as exc:
        raise ApiError('Xray create request schema is unavailable') from exc
    properties = schema.get('properties')
    if not isinstance(properties, dict) or not properties:
        raise ApiError('Xray create request has no verified properties')
    return {name:schema_object(cb.spec, value) for name,value in properties.items()
            if schema_object(cb.spec,value).get('readOnly') is not True}


def save_field(wizard, category, source, target, conversion, mapping, samples):
    properties = writable_properties(wizard.cb, category)
    if target not in properties:
        raise ApiError('Target is not a writable property of the verified Xray request')
    if conversion not in CONVERTERS or not isinstance(mapping, dict):
        raise ApiError('Invalid Xray conversion/value mapping')
    current = wizard.config['migration'].get(category+'_adapter', {}).get('fields', {})
    if any(key != source and spec.get('target') == target for key,spec in current.items()):
        raise ApiError('Target request property already mapped')
    from single_import import validate
    for sample in samples:
        validate(wizard.cb.spec, convert(sample, conversion, mapping), properties[target])
    spec = {'target':target,'type':conversion,'value_mapping':deepcopy(mapping)}
    wizard.config['migration'].setdefault(category+'_adapter', {}).setdefault('fields', {})[source] = spec
    saved = {kind:deepcopy(wizard.config['migration'].get(kind+'_adapter', {}).get('fields', {})) for kind in CATEGORIES}
    wizard.state.save('wizard','xray-record-mapping', saved)
    return spec


def mapping_rows(report, settings):
    """Different forms for Run values, Run results, and Test Case procedures."""
    rows=[]
    groups=[]
    if report.get('kind')=='run':
        groups.append(('run',[report['run']]))
        groups.append(('run_step',report.get('reads',{}).get('test_run_steps',{}).get('items',[])))
    elif report.get('kind')=='test':
        groups.append(('test_step',report.get('reads',{}).get('test_steps',{}).get('items',[])))
    for category, records in groups:
        if not all(isinstance(r,dict) for r in records):
            raise ApiError('Record mapping requires verified object arrays')
        sources = sorted({key for record in records for key in record})
        if category=='run':
            sources += ['_testItem','_executionItem','_executionSource']
        for source in dict.fromkeys(sources):
            samples=[record[source] for record in records if source in record]
            spec=settings.get(category+'_adapter',{}).get('fields',{}).get(source,{})
            rows.append({'category':category,'source':source,'samples':samples,'spec':deepcopy(spec)})
    return rows
