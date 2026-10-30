import unittest
from types import SimpleNamespace
from unittest.mock import patch
from mapping_helper import collect_catalog, build_spec, apply_overlay, validate_overlay
from migration.fields import payload


FIELD = {'id': 5, 'label': 'Result', 'typeId': 7, 'mandatory': True,
         'multipleSelection': False, 'options': [{'id': 9, 'name': 'Pass'}], 'reference': None}


class MappingHelperTests(unittest.TestCase):
    def test_read_only_collection_and_no_mutation(self):
        config = {'execution_issue_types': ['Test Execution']}
        calls = []
        issue = {'key': 'P-1', 'fields': {'issuetype': {'id': '1', 'name': 'Test'}, 'summary': 'sample'}}
        class Reader:
            def __init__(self, http, configuration):
                calls.append(configuration)
            def issues(self, project): return iter([issue, issue])
        jira = SimpleNamespace(config=config, http=None, fields=lambda: [{'id': 'summary', 'name': 'Summary'}],
                               projects=lambda: [{'key': 'P'}])
        cb = SimpleNamespace(fields=lambda tracker: [FIELD], configuration=lambda tracker: {'fields': [FIELD]})
        with patch('mapping_helper.JiraClient', Reader):
            result = collect_catalog(jira, cb, [100], 1)
        self.assertEqual(result['projects'][0]['sample_count'], 1)
        self.assertEqual(result['targets'][0]['fields'][0]['id'], 5)
        self.assertFalse(result['values_complete'])
        self.assertNotIn('execution_issue_types', calls[0])
        self.assertEqual(config, {'execution_issue_types': ['Test Execution']})

    def test_field_local_value_maps_do_not_collide(self):
        a = build_spec('a', FIELD, 'option', 'a', {'1': 9})
        b = build_spec('b', dict(FIELD, id=6), 'option', 'b', {'1': 10})
        self.assertEqual(payload({'a': {'id': '1'}, 'b': {'id': '1'}}, {'a': a, 'b': b}, {}), {'a': 9, 'b': 10})

    def test_missing_server_type_not_guessed(self):
        with self.assertRaises(ValueError):
            build_spec('a', {'id': 5}, 'text', 'name', {})
        with self.assertRaises(ValueError):
            build_spec('a', FIELD, 'option', 'result', {})

    def test_overlay_preserves_credentials_and_does_not_modify_input(self):
        original = {'jira': {'url': 'https://example.test', 'token_env': 'JIRA_TOKEN'},
                    'migration': {'field_mapping': {'existing': {}}, 'mode': 'full-xray'}}
        overlay = {'migration': {'field_mapping': {'P:1': {'summary': build_spec('summary', FIELD, 'text', 'name', {})}}}}
        result = apply_overlay(original, overlay)
        self.assertEqual(result['jira'], original['jira'])
        self.assertNotIn('P:1', original['migration']['field_mapping'])
        self.assertIn('existing', result['migration']['field_mapping'])
        with self.assertRaises(ValueError):
            apply_overlay(original, {'migration': {'mode': 'apply'}})
        with self.assertRaises(ValueError):
            apply_overlay(original, {'jira': {'password': 'not-accepted'}})

    def test_validation_finds_unmapped_choice_and_required_fields(self):
        catalog = {'projects': [{'key': 'P', 'types': [{'id': '1', 'fields': {'result': [{'id': '2'}], 'summary': ['s']}}]}],
                   'targets': [{'id': 100, 'fields': [FIELD, dict(FIELD, id=6, label='Required')]}]}
        spec = build_spec('result', FIELD, 'option', 'result', {'1': 9})
        overlay = {'migration': {'field_mapping': {'P:1': {'result': spec}}, 'tracker_mapping': {'P:1': 100}}}
        errors = validate_overlay(overlay, catalog)
        self.assertTrue(any('summary' in e for e in errors))
        self.assertTrue(any('값 매핑' in e for e in errors))
        self.assertTrue(any('Required' in e for e in errors))

    def test_empty_source_is_not_ready(self):
        self.assertTrue(validate_overlay({'migration': {}}, {'projects': [], 'targets': []}))

    def test_error_identifies_server_and_stage(self):
        from clients.http import ApiError
        from mapping_helper import read_stage
        def failure():
            raise ApiError('GET request failed: HTTP 500')
        with self.assertRaisesRegex(ApiError, 'Codebeamer 트래커 123 설정: GET request failed: HTTP 500'):
            read_stage('Codebeamer 트래커 123 설정', failure)

    def test_one_tracker_failure_preserves_other_trackers(self):
        from clients.http import ApiError
        jira = SimpleNamespace(config={}, http=None, fields=lambda: [], projects=lambda: [])
        def fields(tracker):
            if tracker == 1:
                raise ApiError('GET request failed: HTTP 500')
            return [FIELD]
        cb = SimpleNamespace(fields=fields, configuration=lambda tracker: {'fields': [FIELD]})
        result = collect_catalog(jira, cb, [1, 2])
        self.assertEqual([t['id'] for t in result['targets']], [2])
        self.assertIn('트래커 1 필드 목록', result['warnings'][0])
        self.assertTrue(validate_overlay({'migration': {}}, result))

    def test_configuration_failure_keeps_field_definitions_with_warning(self):
        from clients.http import ApiError
        jira = SimpleNamespace(config={}, http=None, fields=lambda: [], projects=lambda: [])
        def failure(tracker):
            raise ApiError('GET request failed: HTTP 500')
        cb = SimpleNamespace(fields=lambda tracker: [FIELD], configuration=failure)
        result = collect_catalog(jira, cb, [2])
        self.assertEqual(result['targets'][0]['fields'], [FIELD])
        self.assertIn('트래커 2 설정', result['warnings'][0])

    def test_diagnostics_excludes_payload_and_exception_secrets(self):
        import tempfile
        from pathlib import Path
        from mapping_helper import Diagnostics, read_stage
        from clients.http import ApiError
        with tempfile.TemporaryDirectory() as directory:
            diagnostics = Diagnostics(directory)
            def failure():
                raise ApiError('HTTP 500 password=SECRET-TOKEN https://private.example/path')
            with self.assertRaises(ApiError):
                read_stage('Codebeamer 트래커 123 설정', failure, diagnostics)
            read_stage('필드 조회', lambda: {'private_value': 'SECRET-PAYLOAD'}, diagnostics)
            contents = '\n'.join(p.read_text(encoding='utf-8') for p in diagnostics.root.iterdir())
            for secret in ('SECRET-TOKEN', 'SECRET-PAYLOAD', 'private.example', 'private_value'):
                self.assertNotIn(secret, contents)
            summary = (diagnostics.root / 'summary.txt').read_text(encoding='utf-8')
            self.assertIn('HTTP 500', summary)
            self.assertIn('트래커 123 설정', summary)
            self.assertLessEqual(len(summary.splitlines()), 10)

    def test_diagnostics_summary_bounded_and_runs_separate(self):
        import tempfile
        from mapping_helper import Diagnostics
        with tempfile.TemporaryDirectory() as directory:
            first, second = Diagnostics(directory), Diagnostics(directory)
            self.assertNotEqual(first.root, second.root)
            for _ in range(50):
                first.record('화면 구성', 'error', error=KeyError('private-data'))
            self.assertLessEqual(len((first.root / 'summary.txt').read_text(encoding='utf-8').splitlines()), 10)

    def test_existing_mapping_loaded_and_saved_mapping_wins_without_mutation(self):
        from mapping_helper import existing_overlay, scope_mappings
        config = {'migration': {'field_mapping': {'Test': {'summary': {'target': 'original'}}},
                               'tracker_mapping': {'P:Test': 100}, 'run_adapter': {'fields': {'status': {'target': 'status'}}},
                               'user_mapping': {'legacy': 1}}}
        saved = {'migration': {'field_mapping': {'P:1': {'summary': {'target': 'saved'}}}}}
        overlay = existing_overlay(config, saved)
        self.assertEqual(scope_mappings(overlay, {'key': 'P'}, {'id': '1', 'name': 'Test'})['summary']['target'], 'saved')
        self.assertEqual(config['migration']['field_mapping']['Test']['summary']['target'], 'original')
        self.assertNotIn('user_mapping', overlay['migration'])
        self.assertEqual(overlay['migration']['run_adapter']['fields']['status']['target'], 'status')

    def test_new_suggestions_are_not_saved_until_confirmed(self):
        from mapping_helper import existing_overlay, suggest_scope, confirm_row
        config = {'migration': {}}
        overlay = existing_overlay(config)
        kind = {'id': '1', 'name': 'Test', 'fields': {'summary': ['sample']}}
        field = dict(FIELD, label='Name', name='name', options=[])
        proposals = suggest_scope(kind, {'summary': {'name': 'Summary', 'schema': {'type': 'string'}}}, [field], config, {})
        self.assertTrue(proposals['summary']['ready'])
        self.assertEqual(overlay['migration']['field_mapping'], {})
        confirm_row(overlay, {'key': 'P'}, kind, 'summary', 100, proposals['summary']['spec'])
        self.assertEqual(overlay['migration']['field_mapping']['P:1']['summary']['target'], 'name')

    def test_ambiguous_fields_never_auto_selected(self):
        from mapping_helper import suggest_mapping
        result = suggest_mapping('summary', {'schema': {'type': 'string'}}, ['x'],
                                 [dict(FIELD, label='Name'), dict(FIELD, id=6, label='Name')], {})
        self.assertFalse(result['ready'])
        self.assertIsNone(result['field'])

    def test_source_alias_collision_and_existing_target_prevent_bulk_confirmation(self):
        from mapping_helper import suggest_scope
        field = dict(FIELD, label='Name', options=[])
        kind = {'fields': {'summary': ['x'], 'title': ['y']}}
        suggestions = suggest_scope(kind, {}, [field], {}, {})
        self.assertTrue(all(not p['ready'] for p in suggestions.values()))
        suggestions = suggest_scope({'fields': {'summary': ['x']}}, {}, [field], {}, {'other': {'field_id':5}})
        self.assertFalse(suggestions['summary']['ready'])

    def test_value_suggestions_use_display_names_not_equal_ids(self):
        from mapping_helper import suggest_values
        values, missing = suggest_values([{'id': '1', 'name': 'Passed'}, {'id': '2', 'name': 'Unknown'}],
                                         [{'id': 99, 'name': 'Passed'}, {'id': 2, 'name': 'Failed'}])
        self.assertEqual(values, {'Passed': {'id': 99}})
        self.assertEqual(missing, ['Unknown'])
        values, missing = suggest_values([{'id':1, 'name':'Same'}], [{'id':2,'name':'Same'}, {'id':3,'name':'Same'}])
        self.assertEqual(values,{})
        self.assertEqual(missing,['Same'])

    def test_no_guessed_custom_payload_key_or_system_field_auto_confirmation(self):
        from mapping_helper import suggest_mapping
        custom = dict(FIELD, label='Build', options=[])
        result = suggest_mapping('build', {'schema':{'type':'string'}}, ['x'], [custom], {})
        self.assertFalse(result['ready'])
        self.assertEqual(result['target_key'],'')
        system = dict(FIELD,label='Submitted at', options=[])
        settings = {'codebeamer':{'field_bindings':{'created':{'fieldId':5}}}}
        result = suggest_mapping('created', {'schema':{'type':'datetime'}}, ['2026-01-01T00:00:00Z'], [system], settings)
        self.assertFalse(result['ready'])

    def test_tracker_change_does_not_overwrite_existing_mapping(self):
        from mapping_helper import confirm_row
        overlay={'migration':{'tracker_mapping':{'P:1':100},'field_mapping':{'P:1':{'summary':{'field_id':5}}}}}
        with self.assertRaises(ValueError):
            confirm_row(overlay,{'key':'P'},{'id':'1','name':'Test'},'summary',200,{'field_id':6})
        self.assertEqual(overlay['migration']['field_mapping']['P:1']['summary']['field_id'],5)

    def test_inherited_global_value_maps_are_visible_without_modifying_config(self):
        from mapping_helper import existing_overlay
        config={'migration':{'field_mapping':{'Test':{'status':{'type':'status','target':'status'}}},
                             'status_mapping':{'Open':{'id':1}}}}
        overlay=existing_overlay(config)
        self.assertEqual(overlay['migration']['field_mapping']['Test']['status']['value_mapping'],{'Open':{'id':1}})
        self.assertNotIn('value_mapping',config['migration']['field_mapping']['Test']['status'])

    def test_manual_confirmation_cannot_overwrite_another_sources_target(self):
        from mapping_helper import confirm_row
        overlay={'migration':{'tracker_mapping':{'P:1':100},'field_mapping':{'P:1':{'summary':{'field_id':5,'target':'name'}}}}}
        with self.assertRaises(ValueError):
            confirm_row(overlay,{'key':'P'},{'id':'1','name':'Test'},'title',100,{'field_id':5,'target':'name'})
        self.assertNotIn('title',overlay['migration']['field_mapping']['P:1'])

    def test_similar_custom_name_is_review_only(self):
        from mapping_helper import suggest_mapping
        field=dict(FIELD,label='Build Number',options=[])
        settings={'codebeamer':{'field_bindings':{'buildNumber':{'fieldId':5}}}}
        result=suggest_mapping('buildNumbers',{'schema':{'type':'string'}},['b1'],[field],settings)
        self.assertEqual(result['field'],field)
        self.assertFalse(result['ready'])
        self.assertIn('유사 이름',result['reason'])

    def test_partial_saved_scope_keeps_other_config_fields(self):
        from mapping_helper import existing_overlay, scope_mappings
        config={'migration':{'field_mapping':{'P:1':{'summary':{'target':'old'},'description':{'target':'description'}},
                                               'Test':{'priority':{'target':'priority'}}}}}
        saved={'migration':{'field_mapping':{'P:1':{'summary':{'target':'new'}}}}}
        overlay=existing_overlay(config,saved)
        mappings=scope_mappings(overlay,{'key':'P'},{'id':'1','name':'Test'})
        self.assertEqual(mappings['summary']['target'],'new')
        self.assertEqual(mappings['description']['target'],'description')
        self.assertEqual(mappings['priority']['target'],'priority')

    def test_default_field_filter_uses_screen_fields_and_preserves_existing(self):
        from mapping_helper import visible_sources
        kind={'fields':{'summary':['a'],'customfield_1':[],'customfield_2':['hidden'],
                        'customfield_3':[],'votes':[{'votes':1}],'_sourceEvent':[]},
              'screen_fields':['summary','customfield_1']}
        self.assertEqual(visible_sources(kind,{'customfield_3':{}}),['summary','customfield_1','customfield_3'])
        self.assertEqual(visible_sources(kind,{},True),list(kind['fields']))

    def test_field_filter_fallback_does_not_claim_jira_screen_equivalence(self):
        from mapping_helper import visible_sources
        kind={'fields':{'summary':[None],'customfield_1':[],'customfield_2':['has value'],'votes':[1]}}
        self.assertEqual(visible_sources(kind,{}),['summary','customfield_2'])

    def creation_client(self, fail=False, changed=False):
        from copy import deepcopy
        from clients.http import ApiError
        baseline={'basicInformation':{'trackerId':100},'fields':[dict(FIELD,permission={'type':'UNRESTRICTED'})]}
        class Client:
            write=False
            spec=None
            writes=0
            def discover(self): self.spec={};return []
            def operation(self,name): return {}
            def configuration(self,tracker):
                if changed and not self.writes:return dict(baseline,changed=True)
                return deepcopy(self.actual if self.writes else baseline)
            def call(self,name,params,body):
                self.writes+=1
                if fail:raise ApiError('POST request failed: HTTP 500')
                self.assertion=(body['fields'][:-1]==baseline['fields'])
                self.actual=deepcopy(body);self.actual['fields'][-1]['id']=99
                return {}
        return Client(),baseline

    def test_field_creation_needs_approval_and_preserves_existing_fields(self):
        from mapping_helper import create_confirmed_field, creation_templates
        from clients.http import ApiError
        from pathlib import Path
        import tempfile
        cb,baseline=self.creation_client()
        template=creation_templates(baseline)[0]['definition'];template['label']='New field'
        self.assertNotIn('id',template)
        self.assertFalse(template['mandatory'])
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'actions.json'
            with self.assertRaises(ApiError):create_confirmed_field(cb,100,template,baseline,path)
            self.assertEqual(cb.writes,0)
            created,_=create_confirmed_field(cb,100,template,baseline,path,approved=True)
            self.assertEqual(created['id'],99)
            self.assertTrue(cb.assertion)
            self.assertFalse(cb.write)
            with self.assertRaises(ApiError):create_confirmed_field(cb,100,template,baseline,path,approved=True)
            self.assertEqual(cb.writes,1)

    def test_changed_configuration_blocks_field_creation(self):
        from mapping_helper import create_confirmed_field, creation_templates
        from clients.http import ApiError
        from pathlib import Path
        import tempfile
        cb,baseline=self.creation_client(changed=True)
        template=creation_templates(baseline)[0]['definition'];template['label']='New field'
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ApiError):create_confirmed_field(cb,100,template,baseline,Path(directory)/'a.json',approved=True)
        self.assertEqual(cb.writes,0)

    def test_uncertain_create_is_not_retried_and_write_permission_restored(self):
        from mapping_helper import create_confirmed_field, creation_templates
        from clients.http import ApiError
        from pathlib import Path
        import tempfile,json
        cb,baseline=self.creation_client(fail=True)
        template=creation_templates(baseline)[0]['definition'];template['label']='New field'
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'a.json'
            with self.assertRaises(ApiError):create_confirmed_field(cb,100,template,baseline,path,approved=True)
            self.assertFalse(cb.write)
            self.assertEqual(next(iter(json.loads(path.read_text()).values()))['status'],'pending')
            with self.assertRaises(ApiError):create_confirmed_field(cb,100,template,baseline,path,approved=True)
        self.assertEqual(cb.writes,1)

    def test_jira_editmeta_is_collected_per_type_without_exporting_values(self):
        from mapping_helper import collect_catalog
        issue={'key':'P-1','fields':{'issuetype':{'id':'1','name':'Test'},'summary':'sample','customfield_1':None}}
        class Reader:
            def __init__(self,*args):pass
            def issues(self,project):return iter([issue])
        class Http:
            calls=[]
            def request(self,method,path):
                self.calls.append((method,path))
                return {'fields':{'summary':{'name':'Summary'},'customfield_1':{'name':'Build'}}}
        http=Http()
        jira=SimpleNamespace(config={},http=http,fields=lambda:[],projects=lambda:[{'key':'P'}])
        cb=SimpleNamespace(fields=lambda tracker:[FIELD],configuration=lambda tracker:{'fields':[FIELD]})
        with patch('mapping_helper.JiraClient',Reader):
            result=collect_catalog(jira,cb,[100])
        kind=result['projects'][0]['types'][0]
        self.assertEqual(kind['screen_fields'],['summary','customfield_1'])
        self.assertEqual(http.calls,[('GET','/rest/api/2/issue/P-1/editmeta')])

    def test_create_uses_verified_codebeamer_operation_and_native_envelope(self):
        from mapping_helper import create_confirmed_field, creation_templates
        from clients.codebeamer_client import CodebeamerClient
        from copy import deepcopy
        from pathlib import Path
        import tempfile
        baseline={'basicInformation':{'trackerId':100},'fields':[dict(FIELD,permission={'type':'UNRESTRICTED'})]}
        class Http:
            body=None
            def request(self,method,path,data=None,**kwargs):
                if method=='POST':
                    self.body=deepcopy(data)
                    self.actual=deepcopy(data);self.actual['fields'][-1]['id']=101
                    return {}
                return deepcopy(self.actual if self.body else baseline)
        http=Http()
        cb=CodebeamerClient(http,{'operations':{'tracker_configuration_update':{'operationId':'postTrackerConfiguration'}}})
        cb.spec={'paths':{'/v3/tracker/configuration':{'post':{'operationId':'postTrackerConfiguration'}}}}
        proposal=creation_templates(baseline)[0]['definition'];proposal['label']='Build'
        with tempfile.TemporaryDirectory() as directory:
            created,_=create_confirmed_field(cb,100,proposal,baseline,Path(directory)/'journal.json',approved=True)
        self.assertEqual(created['id'],101)
        self.assertEqual(http.body['basicInformation'],baseline['basicInformation'])
        self.assertEqual(http.body['fields'][:-1],baseline['fields'])
        self.assertFalse(cb.write)

    def test_allowed_values_are_proposed_even_if_not_in_sample(self):
        from mapping_helper import source_observations
        kind={'fields':{'customfield_1':[]},'definitions':{'customfield_1':{'allowedValues':[{'id':'1','value':'A'},{'id':'2','value':'B'}]}}}
        self.assertEqual(len(source_observations(kind,'customfield_1')),2)
        self.assertEqual(kind['fields']['customfield_1'],[])

    def test_configuration_failure_disables_field_creation_templates(self):
        from mapping_helper import creation_templates
        self.assertEqual(creation_templates({'fields':[]}),[])
        self.assertEqual(creation_templates({'fields':[dict(FIELD,reference={'tracker':1})]}),[])

    def test_auto_connect_applies_known_mapping_without_confirmation(self):
        from mapping_helper import auto_connect_scope,existing_overlay
        settings={'migration':{'tracker_mapping':{'P:1':100}}}
        overlay=existing_overlay(settings)
        kind={'id':'1','name':'Test','fields':{'summary':['x']}}
        applied=auto_connect_scope(overlay,{'key':'P'},kind,[dict(FIELD,label='Name',options=[])],{'summary':{'schema':{'type':'string'}}},settings)
        self.assertEqual(applied,['summary'])
        self.assertEqual(overlay['migration']['field_mapping']['P:1']['summary']['target'],'name')

    def option_client(self, fail=False):
        from copy import deepcopy
        from clients.http import ApiError
        from clients.codebeamer_client import CodebeamerClient
        baseline={'basicInformation':{'trackerId':100},'fields':[dict(FIELD,permission={'type':'UNRESTRICTED'})]}
        class Http:
            writes=0
            def request(self,method,path,data=None,**kwargs):
                if method=='POST':
                    self.writes+=1
                    if fail:raise ApiError('POST request failed: HTTP 500')
                    self.actual=deepcopy(baseline)
                    self.actual['fields'][0]['options'].append({'id':1000,'name':data['name']})
                    return {'id':1000,'name':data['name']}
                current=deepcopy(self.actual if self.writes else baseline)
                return current['fields'] if path.endswith('/fields') else current
        http=Http()
        cb=CodebeamerClient(http,{})
        cb.spec={'paths':{'/actual/trackers/{trackerId}/fields/{fieldId}/choices':{'post':{
            'operationId':'createActualChoice','requestBody':{'content':{'application/json':{'schema':{
                'type':'object','required':['name'],'properties':{'name':{'type':'string'},'id':{'type':'integer','readOnly':True}}}}}}}}}}
        return cb,http,baseline

    def test_missing_option_created_and_server_id_used(self):
        from mapping_helper import create_missing_option
        from pathlib import Path
        import tempfile
        cb,http,baseline=self.option_client()
        with tempfile.TemporaryDirectory() as directory:
            created,actual=create_missing_option(cb,100,baseline['fields'][0],'Failed',Path(directory)/'a.json')
            self.assertEqual(created,{'id':1000,'name':'Failed'})
            self.assertEqual(actual['fields'][0]['options'][0],FIELD['options'][0])
            self.assertEqual(http.writes,1)
            created,_=create_missing_option(cb,100,actual['fields'][0],'Failed',Path(directory)/'a.json')
            self.assertEqual(http.writes,1)
        self.assertFalse(cb.write)
        self.assertNotIn('helper_option_create',cb.config['operations'])

    def test_uncertain_option_create_is_not_retried(self):
        from mapping_helper import create_missing_option
        from pathlib import Path
        from clients.http import ApiError
        import tempfile
        cb,http,baseline=self.option_client(fail=True)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'a.json'
            with self.assertRaises(ApiError):create_missing_option(cb,100,baseline['fields'][0],'Failed',path)
            http.writes=0
            with self.assertRaises(ApiError):create_missing_option(cb,100,baseline['fields'][0],'Failed',path)
            self.assertEqual(http.writes,0)
        self.assertFalse(cb.write)

    def test_option_contract_does_not_invent_required_ids(self):
        from mapping_helper import option_payload
        from clients.http import ApiError
        with self.assertRaises(ApiError):option_payload({}, {'type':'object','required':['name','id'],'properties':{'name':{'type':'string'},'id':{'type':'integer'}}},'Failed')

    def test_direct_choice_api_does_not_need_failing_tracker_configuration(self):
        from mapping_helper import create_missing_option
        from pathlib import Path
        import tempfile
        from clients.http import ApiError
        cb,http,baseline=self.option_client()
        original=http.request
        def request(method,path,*args,**kwargs):
            if path.endswith('/configuration'):raise ApiError('GET request failed: HTTP 500')
            return original(method,path,*args,**kwargs)
        http.request=request
        with tempfile.TemporaryDirectory() as directory:
            created,actual=create_missing_option(cb,100,baseline['fields'][0],'Failed',Path(directory)/'a.json')
        self.assertEqual(created['id'],1000)
        self.assertTrue(actual['_fields_only'])

    def test_option_creation_via_verified_configuration_schema(self):
        from mapping_helper import create_missing_option
        from clients.codebeamer_client import CodebeamerClient
        from pathlib import Path
        from copy import deepcopy
        import tempfile
        baseline={'basicInformation':{'trackerId':100},'fields':[dict(FIELD,permission={'type':'UNRESTRICTED'})]}
        class Http:
            writes=0
            def request(self,method,path,data=None,**kwargs):
                if method=='POST':
                    self.writes+=1;self.actual=deepcopy(data);self.actual['fields'][0]['options'][-1]['id']=77;return {}
                return deepcopy(self.actual if self.writes else baseline)
        http=Http()
        cb=CodebeamerClient(http,{'operations':{'tracker_configuration_update':{'operationId':'postTrackerConfiguration'}}})
        choice={'type':'object','required':['name'],'properties':{'name':{'type':'string'}}}
        schema={'type':'object','properties':{'fields':{'type':'array','items':{'type':'object','properties':{'options':{'type':'array','items':choice}}}}}}
        cb.spec={'paths':{'/actual/config':{'post':{'operationId':'postTrackerConfiguration','requestBody':{'content':{'application/json':{'schema':schema}}}}}}}
        with tempfile.TemporaryDirectory() as directory:
            created,actual=create_missing_option(cb,100,baseline['fields'][0],'Failed',Path(directory)/'a.json')
        self.assertEqual(created['id'],77)
        self.assertEqual(http.writes,1)
        self.assertEqual(actual['fields'][0]['options'][0],baseline['fields'][0]['options'][0])
