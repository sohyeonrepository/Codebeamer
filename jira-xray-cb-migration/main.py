import argparse
import json
from pathlib import Path
from clients.http import Http, ApiError
from clients.jira_client import JiraClient
from clients.xray_client import XrayClient
from clients.codebeamer_client import CodebeamerClient
from migration.state import State
from migration.engine import Engine


def main():
    parser = argparse.ArgumentParser(description='Jira DC / Xray → Codebeamer')
    parser.add_argument('command', choices=['discover', 'migrate', 'diagnose-history', 'diagnose-connection', 'list-issue-types', 'prepare-run-migration'])
    parser.add_argument('--config', default='config.json')
    parser.add_argument('--prompt-auth', action='store_true', help='Prompt for missing credentials; never save them')
    parser.add_argument('--tracker-id', type=int, help='Tracker to inspect with diagnose-connection')
    parser.add_argument('--apply', action='store_true', help='Permit target writes')
    parser.add_argument('--item-id', type=int)
    parser.add_argument('--probe-payload', help='Validated update request JSON file')
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding='utf-8'))
    state = State(config['migration'].get('state_dir', '.migration'))
    log = state.root / 'requests.jsonl'
    try:
        def http(settings):
            return Http(settings['url'], settings.get('token_env'), log,
                        auth=settings.get('auth', 'bearer'), username_env=settings.get('username_env'),
                        password_env=settings.get('password_env'), allow_http=settings.get('allow_http', False),
                        prompt_auth=args.prompt_auth)
        jira = JiraClient(http(config['jira']), config['jira'], state.save)
        if args.command == 'list-issue-types':
            from migration.configuration import mapping_catalog
            catalog = mapping_catalog(jira, config['migration'])
            report_path = state.root / 'tracker-mapping-template.json'
            report_path.write_text(json.dumps({
                'project_mapping': {p['project']: config['migration'].get('project_mapping', {}).get(p['project']) for p in catalog},
                'tracker_mapping': {p['config_key']: p['tracker_id'] for p in catalog},
                'issue_types': catalog}, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({'issue_types': catalog, 'template': str(report_path), 'writes': 0}, ensure_ascii=False))
            return 0
        cb = CodebeamerClient(http(config['codebeamer']), config['codebeamer'], args.apply)
        identity = {'jira': config['jira']['url'], 'codebeamer': config['codebeamer']['url']}
        prior = state.get('environment-binding')
        if prior and prior[1] != identity:
            raise ApiError('State belongs to different source/target URLs; use a separate state_dir')
        state.mark('environment-binding', 'done', identity)
        if args.command == 'prepare-run-migration':
            from migration.records import run_tracker_id
            tracker = run_tracker_id(config['migration'])
            # Save accessible tracker evidence even if Swagger discovery fails.
            configuration = cb.configuration(tracker)
            field_list = cb.fields(tracker)
            state.save('discovery', 'run-tracker-configuration', configuration)
            state.save('discovery', 'run-tracker-fields', field_list)
            operations = cb.discover()
            (state.root / 'run-api-capabilities.json').write_text(json.dumps({
                'tracker_id': tracker, 'tracker_configuration': configuration, 'tracker_fields': field_list,
                'operations': operations, 'openapi': cb.spec}, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({'writes': 0, 'capabilities': str(state.root / 'run-api-capabilities.json'),
                              'operations': len(operations)}))
            return 0
        if args.command == 'diagnose-connection':
            if not args.tracker_id:
                parser.error('--tracker-id required for diagnose-connection')
            fields = jira.fields()
            state.save('diagnostics', 'jira-fields', fields)
            configuration = cb.configuration(args.tracker_id)
            target_fields = cb.fields(args.tracker_id)
            state.save('diagnostics', 'tracker-configuration', configuration)
            state.save('diagnostics', 'tracker-fields', target_fields)
            print(json.dumps({'jira': 'ok', 'codebeamer': 'ok', 'tracker_id': args.tracker_id,
                              'jira_field_count': len(fields), 'writes': 0}))
            return 0
        if args.command == 'discover':
            fields, projects, operations = jira.fields(), jira.projects(), cb.discover()
            for name, data in [('jira-fields', fields), ('jira-projects', projects), ('cb-openapi', cb.spec), ('cb-operations', operations)]:
                state.save('discovery', name, data)
            for tracker in config['migration'].get('tracker_mapping', {}).values():
                if isinstance(tracker, int):
                    state.save('discovery', f'tracker:{tracker}:configuration', cb.configuration(tracker))
                    state.save('discovery', f'tracker:{tracker}:fields', cb.fields(tracker))
            print(json.dumps({'fields': len(fields), 'projects': len(projects), 'operations': len(operations), 'writes': 0}))
            return 0
        if args.command == 'diagnose-history':
            from validation.compare_history import revision_ids
            if not args.item_id:
                parser.error('--item-id required')
            cb.discover()
            before = cb.call('history', {'itemId': args.item_id})
            result = {'before': before, 'native_author_time_restoration': 'unverified', 'writes': 0}
            if args.apply:
                if not args.probe_payload:
                    parser.error('--probe-payload required with --apply')
                body = json.loads(Path(args.probe_payload).read_text(encoding='utf-8'))
                cb.call('item_update', {'itemId': args.item_id}, body)
                after = cb.call('history', {'itemId': args.item_id})
                added = revision_ids(after) - revision_ids(before)
                result.update(after=after, new_revisions=sorted(added), writes=1)
                state.save('diagnostics', 'history-probe', result)
                if len(added) != 1:
                    raise ApiError('Expected exactly one new revision')
            state.save('diagnostics', 'history-probe', result)
            print(json.dumps({'writes': result['writes'], 'native_author_time_restoration': 'unverified'}))
            return 0
        report = Engine(jira, XrayClient(jira.http, config['jira'], state.save), cb, state, config['migration'], args.apply).migrate()
        print(json.dumps({'mode': report['mode'], 'complete': report['complete'], 'issues': len(report['issues']), 'runs': len(report.get('runs', [])),
                          'scope': report.get('scope', 'all'),
                          'errors': len(report['errors']), 'limitations': len(report['limitations']),
                          'report': str(state.root / 'report.json')}, ensure_ascii=False))
        return 0 if report['complete'] else 2
    except (ApiError, KeyError, ValueError, OSError) as exc:
        print(json.dumps({'complete': False, 'error': str(exc) if isinstance(exc, ApiError) else type(exc).__name__}))
        return 2
    finally:
        state.db.close()


if __name__ == '__main__':
    raise SystemExit(main())
