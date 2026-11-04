import unittest
from types import SimpleNamespace
from clients.http import ApiError
from clients.xray_dc_contract import collect_xray_dc
from clients.xray_client import XrayClient

PROFILE = dict(verified=True, items_key='values', total_key='total', identity_key='key',
               offset_param='startAt', limit_param='limit', page_size=1)

class CollectionTests(unittest.TestCase):
    def collect(self, pages, profile=None):
        calls, archived = [], []
        def request(method, path):
            calls.append((method, path))
            return pages.pop(0)
        result = collect_xray_dc(SimpleNamespace(request=request), 'test_plan_tests', 'P-1',
                                profile or PROFILE, lambda *v: archived.append(v))
        return result, calls, archived

    def test_pages_archive_and_readonly(self):
        result, calls, raw = self.collect([{'values':[{'key':'P-2'}], 'total':2},
                                          {'values':[{'key':'P-3'}], 'total':2}])
        self.assertTrue(result['complete'])
        self.assertEqual(len(raw), 2)
        self.assertTrue(all(c[0]=='GET' for c in calls))
        self.assertIn('startAt=1', calls[1][1])

    def test_empty_complete(self):
        self.assertEqual(self.collect([{'values':[], 'total':0}])[0]['items'], [])

    def test_missing_duplicate_drift_and_bad_envelope(self):
        for second in ({'values':[], 'total':2}, {'values':[{'key':'P-2'}], 'total':2},
                       {'values':[{'key':'P-3'}], 'total':3}, {'error':'denied'}):
            with self.subTest(second=second), self.assertRaises(ApiError):
                self.collect([{'values':[{'key':'P-2'}], 'total':2}, second])

    def test_unknown_contract_no_network(self):
        with self.assertRaises(ApiError):
            self.collect([], dict(PROFILE, verified=False))

    def test_explicit_array_and_bound(self):
        self.assertTrue(self.collect([[{'key':'P-2'}]],
                                    dict(verified=True, unpaginated=True, identity_key='key'))[0]['complete'])
        with self.assertRaises(ApiError):
            self.collect([{'values':[{'key':'P-2'}], 'total':2}], dict(PROFILE,max_pages=1))

    def test_merge_keeps_issue_and_source_ids(self):
        issue={'id':'10','key':'P-1','fields':{'summary':'Plan'}}
        http=SimpleNamespace(request=lambda *args: {'values':[{'key':'P-2','id':'11'}],'total':1})
        client=XrayClient(http, {'source_reads':{'plan':{'test_plan_tests':PROFILE}}})
        merged=client.collect_issue_data(issue,'plan')
        self.assertNotIn('_xray_dc', issue)
        self.assertEqual(merged['fields'],issue['fields'])
        self.assertEqual(merged['_xray_dc']['test_plan_tests']['items'][0]['id'],'11')

    def test_http_failure_propagates(self):
        def request(*args): raise ApiError('HTTP 403')
        client=XrayClient(SimpleNamespace(request=request), {'source_reads':{'plan':{'test_plan_tests':PROFILE}}})
        with self.assertRaisesRegex(ApiError,'403'):
            client.collect_issue_data({'key':'P-1'},'plan')

    def test_run_identity_and_raw_fields_preserved(self):
        issue={'key':'P-1','fields':{'runs':[{'testRunId':5,'testKey':'P-2'}]}}
        raw={'id':5,'testKey':'P-2','testExecKey':'P-1','status':'PASS',
             'defects':['P-3'],'startedOn':'2026-01-01','executedBy':'user'}
        record=XrayClient(SimpleNamespace(request=lambda *args:raw)).runs(issue,'runs')[0]
        self.assertEqual(record['data'],raw)
        issue['fields']['runs'] *= 2
        with self.assertRaisesRegex(ApiError,'Duplicate Xray Run'):
            XrayClient(SimpleNamespace(request=lambda *args:raw)).runs(issue,'runs')
        with self.assertRaisesRegex(ApiError,'must be an object'):
            XrayClient(SimpleNamespace(request=lambda *args:[])).runs(issue,'runs')

    def test_run_step_collection_preserves_actual_result_and_evidence(self):
        issue={'key':'P-1','fields':{'runs':[{'testRunId':5,'testKey':'P-2'}]}}
        def request(method,path):
            if path.endswith('/step'):
                return [{'id':7,'actualResult':'observed','evidence':[{'id':8}]}]
            return {'id':5,'testKey':'P-2','testExecKey':'P-1'}
        client=XrayClient(SimpleNamespace(request=request), {'source_reads':{'run':{'test_run_steps':
            dict(verified=True,unpaginated=True,identity_key='id')}}})
        record=client.runs(issue,'runs')[0]
        self.assertEqual(record['dc_collections']['test_run_steps']['items'][0]['evidence'],[{'id':8}])
