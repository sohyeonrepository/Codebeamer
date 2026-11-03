import argparse,json,os
from pathlib import Path
from migrator import Client,read
from full_migration import apply_related_associations

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--output',default='output')
    p.add_argument('--apply',action='store_true')
    p.add_argument('--api-prefix',default='/cb/api')
    a=p.parse_args()
    out=Path(a.output)
    cb=Client(os.environ['CB_URL'],os.environ['CB_USER'],os.environ['CB_TOKEN']) if a.apply else None
    result=apply_related_associations(cb,read(out/'source_issues.json',[]),read(out/'id_map.json',{}),out,
                                      apply=a.apply,api_prefix=a.api_prefix)
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
