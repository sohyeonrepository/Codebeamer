"""Build a Codebeamer ID-resolved phase-2 plan from the two Item batch journals.

No relationship POST/PUT is made. First reconcile source membership and target
IDs, then validate target Test Set table/reference contract before writes.
"""
import argparse
import hashlib
import json
from pathlib import Path

from clients.http import ApiError
from batch_case_set_import import load_events


def stem(jira_url, cb_url, project, kind, tracker):
    identity = "|".join((jira_url, cb_url, project, kind, str(tracker)))
    return hashlib.sha256(identity.encode()).hexdigest()[:18]


def load_relations(path):
    records = {}
    if not path.exists():
        return records
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            raise ApiError("Invalid phase-2 relation journal")
        key = item.get("jira")
        if not isinstance(key,str) or not isinstance(item.get("relations"), list):
            raise ApiError("Invalid phase-2 relation entry")
        records[key] = item
    return records


def resolve_sets(set_items, case_items, set_relations, case_relations):
    by_key = {}
    for key, entry in set_items.items():
        if entry.get("status") == "VERIFIED" and type(entry.get("cb")) is int:
            by_key[key] = ("set",entry["cb"])
    for key, entry in case_items.items():
        if entry.get("status") == "VERIFIED" and type(entry.get("cb")) is int:
            if key in by_key:
                raise ApiError("Same Jira Key in Test Set and Test Case journal")
            by_key[key] = ("case",entry["cb"])
    pairs = {}
    unresolved = []
    for manifest in (set_relations,case_relations):
        for source, entry in manifest.items():
            if source not in by_key:
                unresolved.append({"source":source,"reason":"Source item not VERIFIED"})
                continue
            for relation in entry.get("relations",[]):
                target = relation.get("target_jira_key")
                name = relation.get("relation")
                # Canonical Test Set -> Test Case regardless of source direction.
                if name == "set_contains_test":
                    set_key, case_key = source, target
                elif name == "test_in_set":
                    set_key, case_key = target, source
                else:
                    unresolved.append({"source":source,"target":target,
                                       "relation":name,"reason":"Not Test Set membership / not in scope"})
                    continue
                if set_key not in by_key or case_key not in by_key:
                    unresolved.append({"source":set_key,"target":case_key,
                                       "reason":"Missing verified Item ID"})
                    continue
                if by_key[set_key][0] != "set" or by_key[case_key][0] != "case":
                    unresolved.append({"source":set_key,"target":case_key,
                                       "reason":"Source/target Issue Type mismatch"})
                    continue
                pairs[(set_key,case_key)] = {
                    "test_set_jira_key":set_key,"test_case_jira_key":case_key,
                    "test_set_cb_id":by_key[set_key][1],
                    "test_case_cb_id":by_key[case_key][1],
                    "status":"READY_FOR_RELATION_API_VALIDATION"}
    return list(pairs.values()),unresolved


def main(argv=None):
    parser=argparse.ArgumentParser(description="Resolve Test Set/Test Case CB IDs for phase-2 review")
    parser.add_argument("--config",default="config.local.json")
    parser.add_argument("--jira-project",required=True)
    parser.add_argument("--set-tracker-id",required=True,type=int)
    parser.add_argument("--case-tracker-id",required=True,type=int)
    parser.add_argument("--state-dir",default=".migration/test-case-set-batch")
    args=parser.parse_args(argv)
    if args.set_tracker_id<=0 or args.case_tracker_id<=0:
        parser.error("Tracker IDs must be positive")
    cfg=json.loads(Path(args.config).read_text(encoding="utf-8-sig"))
    root=Path(args.state_dir)
    records={}
    for kind,tracker in (("set",args.set_tracker_id),("case",args.case_tracker_id)):
        hashkey=stem(cfg["jira"]["url"],cfg["codebeamer"]["url"],
                     args.jira_project,kind,tracker)
        records[kind]={
            "items":load_events(root/("batch-"+hashkey+".jsonl")),
            "relations":load_relations(root/("relations-"+hashkey+".jsonl"))}
    ready, unresolved=resolve_sets(records["set"]["items"],records["case"]["items"],
                                   records["set"]["relations"],records["case"]["relations"])
    output=root/"phase2-verified-id-plan.json"
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps({"ready":ready,"unresolved":unresolved,
                                  "note":"Plan only: no CB reference/table/association write yet"},
                                 ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"ready":len(ready),"unresolved":len(unresolved),
                      "file":str(output),"status":"PLAN_ONLY"},ensure_ascii=False))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
