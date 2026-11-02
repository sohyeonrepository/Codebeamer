"""Item-linked source archives, comments, associations and inline attachments.

Native revisions remain distinct from immutable, hash-verified Jira/R4J archives.
All target endpoints are supplied through the existing Swagger operation adapter.
"""
from copy import deepcopy
import hashlib
import json
import re
from urllib.parse import urlsplit
from clients.http import ApiError
from migration.attachments import transfer
from validation.compare_fields import compare_fields


def preflight(cb, settings, plan):
    required = ['project_get', 'tracker_get', 'trackers_get', 'item_create', 'item_get', 'item_update', 'lookup',
                'attachment_upload', 'attachment_get', 'attachment_lookup', 'attachment_metadata_get', 'attachment_url_get',
                'comment_lookup', 'comment_create', 'comment_get',
                'association_lookup', 'association_create', 'association_get']
    if any(e.get('new_tracker') for e in plan['scopes'].values()):
        required.extend(['tracker_create', 'tracker_lookup'])
        op = cb.operation('tracker_create')
        configuration_api = op['method'].upper() == 'POST' and op['path'] == '/v3/tracker/configuration'
        if not configuration_api and ('{projectId}' not in op['path'] or op.get('path_parameters', {}).get('projectId', '${projectId}') != '${projectId}'):
            raise ApiError('Tracker creation must use the selected projectId in its API path')
        if configuration_api:
            from clients.codebeamer_3007 import new_tracker_configuration
            for entry in plan['scopes'].values():
                if entry.get('new_tracker'):
                    new_tracker_configuration(cb,entry['template_tracker'],entry['project_id'],entry['new_tracker'])
    for name in required:
        op = cb.operation(name)
        method = op['method'].upper()
        expected = 'GET' if name.endswith(('_get', '_lookup')) or name == 'lookup' else ('POST', 'PUT', 'PATCH')
        if (isinstance(expected, str) and method != expected) or (isinstance(expected, tuple) and method not in expected):
            raise ApiError('Unexpected API method for ' + name)
    r4j = settings.get('r4j', {})
    for category in ('trees', 'revisions', 'baselines'):
        if not r4j.get(category, {}).get('path'):
            raise ApiError('Configure migration.r4j.' + category + ' with a complete read-only source adapter')
    for entry in plan['scopes'].values():
        for row in entry['rows'].values():
            if row['status'] == 'create':
                profile = settings.get('wizard', {}).get('field_types', {}).get(row['creation']['semantic_type'])
                if not profile or not profile.get('definition') or not profile.get('binding'):
                    raise ApiError('Missing verified field creation template/binding for ' + row['source'])
                # Dedicated field creation validates its schema again against the actual Tracker.
                from mapping_helper import dedicated_field_contract
                definition = deepcopy(profile['definition'])
                definition.update(label=row['creation']['label'], multipleSelection=row['creation']['multipleSelection'])
                if row['creation']['semantic_type'] in ('option', 'status'):
                    definition['options'] = [{'name': label} for label in dict.fromkeys(row['creation']['choices'].values())]
                try:
                    dedicated_field_contract(cb, definition, entry.get('tracker'))
                except ApiError:
                    from clients.codebeamer_3007 import configuration_field_contract
                    configuration_field_contract(cb,definition,entry.get('tracker') or entry.get('template_tracker'))


def collect_r4j(jira, issue, settings, state):
    data = {}
    for category in ('trees', 'revisions', 'baselines'):
        profile = settings.get('r4j', {}).get(category)
        if not profile:
            raise ApiError('Missing R4J ' + category + ' source adapter')
        data[category] = jira.configured_records(profile, {'issueKey': issue['key'],
            'issueId': issue['id'], 'projectKey': issue['fields']['project']['key']})
    issue['_r4j'] = data
    state.save('source-r4j', issue['key'], data)
    return data


def verified_attachment(source_http, cb, state, key, item_id, attachment):
    result = transfer(source_http, cb, state, key, item_id, attachment, reconcile=True)
    metadata = cb.call('attachment_metadata_get', {'itemId': item_id, 'attachmentId': result['id']})
    if metadata.get('filename') != attachment['filename'] or metadata.get('size') != attachment['size']:
        raise ApiError('Target attachment filename/size mismatch')
    # Also revalidate resumed outputs; state alone does not establish target integrity.
    content = cb.call('attachment_get', {'itemId': item_id, 'attachmentId': result['id']}, raw=True)
    if len(content) != result['size'] or hashlib.sha256(content).hexdigest() != result['sha256']:
        raise ApiError('Target attachment integrity mismatch')
    return result


def preserve(cb, state, issue, item_id):
    legacy = {'format': 'jira-legacy-history-v1', 'source_id': issue['id'], 'source_key': issue['key'],
              'native_author_restored': False, 'native_timestamp_restored': False,
              'history': issue['_history'], 'comments': issue['_comments'],
              'worklogs': issue['_worklogs'],
              'linked_issues': issue['fields'].get('issuelinks', []), 'r4j': issue['_r4j'],
              'source_fields': issue['fields']}
    content = json.dumps(legacy, ensure_ascii=False, sort_keys=True, indent=2).encode('utf-8')
    digest = hashlib.sha256(content).hexdigest()
    class BytesSource:
        def request(self, method, path, raw=False):
            return content
    metadata = {'id': 'legacy-' + digest, 'filename': 'jira-legacy-' + str(issue['id']) + '-' + digest[:12] + '.json',
                'size': len(content), 'content': 'memory:legacy'}
    target = verified_attachment(BytesSource(), cb, state, issue['key'], item_id, metadata)
    actual = json.loads(cb.call('attachment_get', {'itemId': item_id, 'attachmentId': target['id']}, raw=True))
    if actual != legacy:
        raise ApiError('Legacy source archive verification failed')
    counts = {'history_source': len(issue['_history']), 'history_preserved': len(actual['history']),
              'history_changes_source': sum(len(h['items']) for h in issue['_history']),
              'history_changes_preserved': sum(len(h['items']) for h in actual['history']),
              'worklogs_source': len(issue['_worklogs']),
              'worklogs_preserved': len(actual['worklogs']),
              'r4j_trees_source': len(issue['_r4j']['trees']),
              'r4j_trees_preserved': len(actual['r4j']['trees']),
              'r4j_revisions_source': len(issue['_r4j']['revisions']),
              'r4j_revisions_preserved': len(actual['r4j']['revisions']),
              'r4j_baselines_source': len(issue['_r4j']['baselines']),
              'r4j_baselines_preserved': len(actual['r4j']['baselines'])}
    state.save('legacy-history', issue['key'], {'item_id': item_id, 'attachment': target, 'counts': counts,
               'native_history': 'Codebeamer-generated revisions', 'legacy_history': 'Jira/R4J source archive'})
    return dict(counts, item_id=item_id, attachment_id=target['id'])


def record(cb, state, category, source_id, source, params):
    """Exact external-ID lookup before create; readback before claiming success."""
    identity = category + ':' + str(source_id)
    found = cb.call(category + '_lookup', dict(params, sourceId=source_id))
    if not isinstance(found, list) or len(found) > 1:
        raise ApiError('Ambiguous ' + category + ' external-ID lookup')
    prior = state.get(identity)
    if found:
        actual_id = found[0]['id']
        if prior and prior[0] == 'done' and prior[1]['id'] != actual_id:
            raise ApiError('Saved child ID disagrees with server lookup')
        state.mark(identity, 'done', {'id': actual_id})
    else:
        if prior:
            raise ApiError('Uncertain/missing target record: reconcile before retry')
        actual_id = state.once(identity, lambda: cb.call(category + '_create', params, dict(source, sourceId=source_id)))['id']
    actual = cb.call(category + '_get', dict(params, recordId=actual_id))
    if compare_fields(source, actual):
        op = cb.operation(category + '_update')
        if op['method'].upper() not in ('POST', 'PUT', 'PATCH'):
            raise ApiError('Invalid record update method')
        fingerprint = hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()
        state.once(identity + ':update:' + fingerprint,
                   lambda: cb.call(category + '_update', dict(params, recordId=actual_id), source))
        actual = cb.call(category + '_get', dict(params, recordId=actual_id))
        if compare_fields(source, actual):
            raise ApiError(category + ' readback mismatch')
    return actual_id


def comments(cb, state, issue, item_id):
    for comment in issue['_comments']:
        # Complete original metadata is also in the item-linked JSON archive.
        body = {'body': comment['body']}
        record(cb, state, 'comment', str(issue['id']) + ':' + str(comment['id']), body, {'itemId': item_id})


def rewrite_description(description, attachments, urls):
    if description is None:
        return None
    if not isinstance(description, str):
        raise ApiError('Structured Description needs a verified rich-text adapter')
    result = description
    by_name = {}
    for attachment in attachments:
        by_name.setdefault(attachment['filename'], []).append(str(attachment['id']))
        aid = str(attachment['id'])
        result = result.replace(attachment['content'], urls[aid])
        pattern = r'(?:https?://[^\s<>"\[\]]+)?/secure/attachment/' + re.escape(aid) + r'/[^\s<>"\[\]|!)]+'
        result = re.sub(pattern, lambda _: urls[aid], result)
    def image(match):
        filename, attrs = match.group(1), match.group(2) or ''
        if filename in by_name:
            if len(by_name[filename]) != 1:
                raise ApiError('Ambiguous inline image filename')
            return '!' + urls[by_name[filename][0]] + attrs + '!'
        if filename.startswith(('http://', 'https://')):
            return match.group(0)
        raise ApiError('Description image missing from attachment inventory')
    result = re.sub(r'!([^!|\n]+)(\|[^!\n]+)?!', image, result)
    if '/secure/attachment/' in result:
        raise ApiError('Unresolved Description attachment reference')
    return result


def attachments(cb, state, source_http, issue, item_id, mapping, settings):
    originals = issue['fields'].get('attachment', [])
    urls = {}
    for attachment in originals:
        result = verified_attachment(source_http, cb, state, issue['key'], item_id, attachment)
        url = cb.call('attachment_url_get', {'itemId': item_id, 'attachmentId': result['id']})['url']
        if urlsplit(url).scheme not in ('http', 'https') or urlsplit(url).username or urlsplit(url).password:
            raise ApiError('Invalid target attachment URL')
        urls[str(attachment['id'])] = url
    if 'description' not in mapping:
        return
    source = issue['fields'].get('description')
    rewritten = rewrite_description(source, originals, urls)
    if rewritten != source:
        from migration.fields import payload
        updated = dict(issue['fields'], description=rewritten)
        body = payload(updated, mapping, settings)
        fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        state.once('inline:' + issue['key'] + ':' + fingerprint, lambda: cb.call('item_update', {'itemId': item_id}, body))
        if compare_fields(body, cb.call('item_get', {'itemId': item_id})):
            raise ApiError('Inline Description link verification failed')
        state.mark('inline-description:' + issue['key'], 'done', {'source': source, 'target': rewritten})


def associations(cb, state, bundles, settings):
    # Both incoming and outgoing representations normalize to the same directed edge.
    seen = set()
    for _, issue in bundles:
        for link in issue['fields'].get('issuelinks', []):
            inward = 'inwardIssue' in link
            other = link.get('inwardIssue') if inward else link.get('outwardIssue')
            if not other:
                raise ApiError('Linked Issue lacks direction/target')
            source_id, target_id = (str(other['id']), str(issue['id'])) if inward else (str(issue['id']), str(other['id']))
            identity = str(link['id'])
            if identity in seen:
                continue
            seen.add(identity)
            source, target = state.get('item:' + source_id), state.get('item:' + target_id)
            if not source or not target or source[0] != 'done' or target[0] != 'done':
                raise ApiError('Association target pending; original relation remains in legacy archive')
            mapping = settings.get('wizard', {}).get('association_types', {})
            type_id = mapping.get(str(link['type']['id']))
            if type(type_id) is not int or type_id <= 0:
                raise ApiError('Missing verified CB Association type for Jira link type ' + str(link['type']['id']))
            body = {'fromItemId': source[1]['id'], 'toItemId': target[1]['id'],
                    'typeId': type_id}
            record(cb, state, 'association', identity, body, {'itemId': source[1]['id']})
