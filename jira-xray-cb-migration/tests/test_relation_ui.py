"""Relation presentation contracts and an optional real Tk interaction check."""
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from import_wizard import reference_field_rows, relation_display, relation_tracker_rows


CATALOG = {
    'jira': [{'id': '1', 'name': 'Blocks', 'outward': 'blocks', 'inward': 'is blocked by'}],
    'codebeamer': [{'id': 9, 'name': 'Blocks'}, {'id': 12, 'name': 'Related'}],
}


class RelationPresentationTests(unittest.TestCase):
    def test_unmapped_stays_blank_even_when_names_match(self):
        self.assertEqual(relation_display(None, CATALOG, {}), ('', '', ''))

    def test_saved_association_uses_exact_id_and_direction(self):
        self.assertEqual(relation_display({'type_id': 12, 'reverse': True}, CATALOG, {}),
                         ('Related · ID 12', 'Association', '반전'))

    def test_missing_saved_type_is_identified_without_replacement(self):
        label, kind, _ = relation_display({'type_id': 20}, CATALOG, {})
        self.assertIn('20', label)
        self.assertIn('목록 미확인', label)
        self.assertEqual(kind, 'Association')

    def test_reference_label_uses_owner_tracker_and_actual_field_id(self):
        mapping = {'mode': 'reference', 'tracker_id': 10, 'target_tracker_id': 20, 'field_id': 7}
        fields = {10: [{'id': 7, 'label': 'Validated by'}], 20: [{'id': 7, 'label': 'Wrong Tracker'}]}
        self.assertEqual(relation_display(mapping, CATALOG, fields),
                         ('Validated by · ID 7', 'Reference', '유지'))

    def test_new_reference_plan_shows_user_label_without_fake_id(self):
        mapping = {'mode': 'reference_create', 'tracker_id': 10, 'target_tracker_id': 20,
                   'creation': {'label': 'Covered by', 'definition': {'label': 'Covered by'}}}
        self.assertEqual(relation_display(mapping, CATALOG, {}),
                         ('Covered by (신규 생성 예정)', 'Reference', '유지'))

    def test_reference_candidates_preserve_hidden_and_raw_metadata(self):
        hidden = {'id': 2, 'label': 'Legacy relation', 'typeName': 'Reference', 'hidden': True}
        configured = {'id': 3, 'label': 'Tests', 'reference': {'trackers': [{'id': 20}]},
                      'multipleSelection': True, 'permission': {'type': 'UNRESTRICTED'}}
        fields = [{'id': 1, 'typeName': 'Text', 'reference': None}, hidden, configured]
        self.assertEqual(reference_field_rows(fields), [hidden, configured])
        self.assertIs(reference_field_rows(fields)[1], configured)

    def test_only_actual_selected_project_trackers_are_candidates(self):
        plan = {'projects': {'A': {'trackers': [{'id': 10, 'name': 'Requirements'}]},
                             'B': {'trackers': [{'id': 10, 'name': 'Requirements'}, {'id': 20, 'name': 'Tests'}]}}}
        self.assertEqual([row['id'] for row in relation_tracker_rows(plan)], [10, 20])
        self.assertEqual(relation_tracker_rows({}), [])


@unittest.skipUnless(os.environ.get('DISPLAY'), 'A GUI display is required for real Tk interaction')
class RelationUISmokeTests(unittest.TestCase):
    def test_saved_selection_can_be_changed_and_cleared_without_import(self):
        import tkinter as tk
        from tkinter import ttk
        from import_wizard import launch
        settings = {'association_types': {'1': {'type_id': 9, 'reverse': True}}}
        writes = []
        wizard = SimpleNamespace(
            plan={'projects': {'P': {'trackers': [{'id': 10, 'name': 'Requirements'}, {'id': 20, 'name': 'Tests'}]}},
                  'scopes': {}, 'stages': {}},
            config={'migration': {'wizard': settings}},
            state=SimpleNamespace(load=lambda *args: CATALOG),
            cb=SimpleNamespace(config={}),
            relation_catalog=lambda: CATALOG,
        )
        def save(source, value):
            writes.append((source, value))
            if value is None:
                settings['association_types'].pop(source, None)
            else:
                settings['association_types'][source] = value
        wizard.save_relation_mapping = save
        errors = []
        original_tk = tk.Tk
        def descendants(widget):
            yield widget
            for child in widget.winfo_children():
                yield from descendants(child)
        class TestWindow(original_tk):
            def __init__(window, *args, **kwargs):
                super().__init__(*args, **kwargs)
                window.after(100, window.exercise)
                window.after(5000, window.destroy)
            def exercise(window):
                try:
                    widgets = list(descendants(window))
                    notebook = next(w for w in widgets if isinstance(w, ttk.Notebook) and
                                    any(w.tab(tab, 'text') == '관계 매핑' for tab in w.tabs()))
                    notebook.select(1)
                    window.update()
                    table = next(w for w in widgets if isinstance(w, ttk.Treeview) and
                                 tuple(w.cget('columns')) == ('source', 'direction', 'mode', 'target', 'reverse'))
                    table.selection_set(table.get_children()[0]); window.update()
                    association_box = next(w for w in widgets if isinstance(w, ttk.Combobox) and
                                           'Related · ID 12' in w.cget('values'))
                    self.assertEqual(association_box.get(), 'Blocks · ID 9')
                    association_box.current(1)
                    next(w for w in widgets if isinstance(w, ttk.Button) and w.cget('text') == '관계 매핑 저장').invoke()
                    window.update()
                    self.assertEqual(settings['association_types']['1']['type_id'], 12)
                    self.assertTrue(settings['association_types']['1']['reverse'])
                    next(w for w in widgets if isinstance(w, ttk.Button) and w.cget('text') == '매핑 해제 (제외)').invoke()
                    window.update()
                    self.assertNotIn('1', settings['association_types'])
                    self.assertEqual(table.set(table.get_children()[0], 'target'), '')
                    self.assertEqual(len(writes), 2)
                except Exception as exc:
                    errors.append(exc)
                finally:
                    window.after(10, window.destroy)
        with patch.object(tk, 'Tk', TestWindow):
            launch(wizard)
        self.assertEqual(errors, [])


if __name__ == '__main__':
    unittest.main()
