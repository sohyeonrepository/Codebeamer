"""Start the existing bounded live-test UI; never approve an Import automatically."""
import json
import sys
from copy import deepcopy
from pathlib import Path


def prepare_config(root):
    destination=root/'config.local.json'
    if destination.exists():
        return destination
    template=json.loads((root/'config.json').read_text(encoding='utf-8-sig'))
    config=deepcopy(template)
    for service in ('jira','codebeamer'):
        config[service]['url']=''
    config['codebeamer'].pop('swagger_url',None)
    # Runtime UI supplies connection details. Preserve schema adapters for review.
    config['migration']['state_dir']='.migration/windows-live-test'
    with destination.open('x',encoding='utf-8') as output:
        json.dump(config,output,ensure_ascii=False,indent=2)
    return destination


def main():
    if sys.version_info < (3,10):
        raise RuntimeError('Python 3.10 or newer is required; Python 3.12 recommended')
    import tkinter
    from import_wizard import main as wizard_main
    root=Path(__file__).resolve().parent
    config=prepare_config(root)
    return wizard_main(['--config',str(config),'--live-test',
                        '--test-state-dir',str(root/'.migration/windows-live-test')])

if __name__=='__main__':
    try:
        main()
    except Exception as exc:
        print('Launch failed:',type(exc).__name__,str(exc),file=sys.stderr)
        raise SystemExit(1)
