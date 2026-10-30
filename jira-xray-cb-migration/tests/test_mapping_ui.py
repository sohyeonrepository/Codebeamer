"""Optional real Tk smoke test. Run with DISPLAY set (e.g. an Xvfb display)."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import mapping_helper as helper


@unittest.skipUnless(os.environ.get('DISPLAY'), 'A GUI display is required for the real Tk smoke test')
class MappingUISmokeTests(unittest.TestCase):
    def test_option_editor_opens_and_saves_without_confirmation(self):
        import tkinter as tk
        from tkinter import ttk, messagebox
        def field(identifier,label,options=None):
            return {'id':identifier,'label':label,'typeId':0,'mandatory':False,
                    'multipleSelection':False,'options':options,'reference':None,'permission':{'type':'UNRESTRICTED'}}
        fields=[field(1,'Name'),field(2,'Status',[{'id':11,'name':'Passed'}])]
        catalog={'definitions':[{'id':'summary','name':'Summary','schema':{'type':'string'}},
                                {'id':'status','name':'Status','schema':{'type':'status'}}],
                 'projects':[{'key':'DEMO','types':[{'id':'1','name':'Test','fields':{
                     'summary':['demo'],'status':[{'id':'1','name':'Passed'},{'id':'2','name':'Failed'}]}}]}],
                 'targets':[{'id':100,'name':'Test Case','fields':fields,'configuration':{}}], 'warnings':[]}
        config={'jira':{'url':'https://example.test','auth':'none'},
                'codebeamer':{'url':'https://example.test','auth':'none','field_bindings':{'status':{'fieldId':2}}},
                'migration':{'tracker_mapping':{'DEMO:1':100}}}
        original_tk=tk.Tk
        errors=[]
        def descendants(widget):
            yield widget
            for child in widget.winfo_children():yield from descendants(child)
        class TestWindow(original_tk):
            def __init__(window,*args,**kwargs):
                super().__init__(*args,**kwargs)
                window.after(700,window.exercise)
                window.after(5000,window.destroy)
            def exercise(window):
                try:
                    tree=next(w for w in descendants(window) if isinstance(w,ttk.Treeview))
                    status=next(item for item in tree.get_children() if tree.item(item,'text').startswith('Status'))
                    tree.selection_set(status);window.update()
                    tree.item(status,open=True);window.update()
                    child=tree.get_children(status)[1]
                    x,y,width,height=tree.bbox(child,'target')
                    tree.event_generate('<Button-1>',x=x+5,y=y+height//2);window.update()
                    box=next(w for w in tree.winfo_children() if isinstance(w,ttk.Combobox))
                    if box.current()!=0:raise AssertionError('Missing option should default to creation')
                    box.current(1);box.event_generate('<<ComboboxSelected>>');window.update()
                    buttons=[w.cget('text') for w in descendants(window) if isinstance(w,ttk.Button) and w.winfo_viewable()]
                    if set(buttons)!={'저장','닫기'}:raise AssertionError('Redundant footer controls are still visible: '+str(buttons))
                    next(w for w in descendants(window) if isinstance(w,ttk.Button) and w.cget('text')=='저장').invoke()
                except Exception as exc:errors.append(exc)
                finally:window.after(10,window.destroy)
        with tempfile.TemporaryDirectory() as directory:
            config_path=Path(directory)/'config.json';config_path.write_text(json.dumps(config),encoding='utf-8')
            output=Path(directory)/'mapping.json'
            args=['mapping_helper.py','--config',str(config_path),'--tracker-id','100','--output',str(output),'--log-dir',str(Path(directory)/'logs')]
            with patch('sys.argv',args),patch.object(helper,'Http',return_value=None),patch.object(helper,'collect_catalog',return_value=catalog),patch.object(helper,'_diagnostics',None),patch.object(tk,'Tk',TestWindow),patch.object(messagebox,'askyesno',side_effect=AssertionError('Unexpected confirmation')),patch.object(messagebox,'showerror',side_effect=lambda *args,**kwargs:errors.append(AssertionError(str(args)))):
                helper.main()
            self.assertEqual(errors,[])
            saved=json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(saved['migration']['field_mapping']['DEMO:1']['status']['value_mapping']['Failed'],{'id':11})
