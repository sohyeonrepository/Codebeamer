"""Transfer recorded Jira comments and binaries to verified CB Item IDs.

CB 3.0 V3 Swagger: POST /v3/items/{itemId}/comments multipart form fields
comment/commentFormat; POST /v3/items/{itemId}/attachments multipart 'attachments'.
Each source child has its own durable attempt journal, without blind write retry.
"""
import argparse
import hashlib
import json
import mimetypes
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from clients.http import ApiError
from post_smoke_test import append_event, make_http
from phase2_resolve_test_sets import stem


def multipart(fields=None, files=None):
    boundary = "cbMigration" + uuid.uuid4().hex
    parts = []
    for name, value in (fields or {}).items():
        if not isinstance(value,str) or not name.isidentifier():
            raise ApiError("Invalid multipart text field")
        parts.extend([f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n".encode(),
                      value.encode("utf-8"),b"\r\n"])
    for name, filename, data in files or []:
        if name != "attachments" or not isinstance(filename,str) or (
            not filename or any(ch in filename for ch in '\r\n\\/"')):
            raise ApiError("Unsafe attachment filename")
        if not isinstance(data,bytes):
            raise ApiError("Attachment must be bytes")
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        header=(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; "
                f"filename=\"{filename}\"\r\nContent-Type: {mime}\r\n\r\n")
        parts.extend([header.encode("utf-8"),data,b"\r\n"])
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), {"Content-Type":"multipart/form-data; boundary="+boundary}


def read_children(path):
    if not path.exists():
        raise ApiError("No captured children manifest. Run Item batch with --capture-children.")
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            raise ApiError("Invalid children manifest JSONL")
        if (not isinstance(row,dict) or not isinstance(row.get("jira"),str) or
            type(row.get("cb")) is not int or
            not isinstance(row.get("comments"),list) or
            not isinstance(row.get("attachments"),list)):
            raise ApiError("Invalid captured children metadata")
        result[row["jira"]] = row
    return result


def read_journal(path):
    records = {}
    if not path.exists():
        return records
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row=json.loads(line)
        except ValueError:
            raise ApiError("Children journal is corrupt")
        identity = row.get("identity")
        if not isinstance(identity,str):
            raise ApiError("Children journal missing identity")
        records[identity]=row
    return records


def validate_attachment_origin(jira_http, url):
    parts=urlsplit(url)
    base=urlsplit(jira_http.base)
    if (parts.scheme,parts.netloc)!=(base.scheme,base.netloc) or parts.username or parts.password:
        raise ApiError("Attachment download URL is not on configured Jira origin")
    return url


def import_comment(cb, item_id, child):
    payload, headers = multipart({"comment":child["body"], "commentFormat":"PlainText"})
    result=cb.request("POST",f"/v3/items/{item_id}/comments",payload,headers=headers)
    comment_id=result.get("id") if isinstance(result,dict) else None
    if type(comment_id) is not int:
        raise ApiError("Comment POST returned without an ID; inspect CB before retry")
    detail=cb.request("GET",f"/v3/items/{item_id}/comments/{comment_id}")
    if not isinstance(detail,dict) or detail.get("comment") != child["body"]:
        raise ApiError("Comment was created but GET verification failed")
    return comment_id


def read_jira_attachment(jira,child,max_bytes):
    size=child.get("size")
    if type(size) is not int or size<0 or size>max_bytes:
        raise ApiError("Attachment size missing or above configured limit: "+child["filename"])
    content_url=child.get("content")
    if not isinstance(content_url,str):
        raise ApiError("Attachment content URL missing")
    raw=jira.request("GET",validate_attachment_origin(jira,content_url),raw=True)
    if not isinstance(raw,bytes) or len(raw)!=size:
        raise ApiError("Jira binary size mismatch")
    return raw


def import_attachment(cb,item_id,child,raw):
    payload, headers=multipart(files=[("attachments",child["filename"],raw)])
    created=cb.request("POST",f"/v3/items/{item_id}/attachments",payload,headers=headers)
    if not isinstance(created,list) or len(created)!=1 or type(created[0].get("id")) is not int:
        raise ApiError("Attachment POST returned without exactly one ID")
    attachment_id=created[0]["id"]
    verified=cb.request("GET",f"/v3/items/{item_id}/attachments/{attachment_id}/content",raw=True)
    if not isinstance(verified,bytes) or hashlib.sha256(verified).digest()!=hashlib.sha256(raw).digest():
        raise ApiError("Uploaded attachment binary GET SHA-256 differs")
    return attachment_id


def main(argv=None):
    parser=argparse.ArgumentParser(description="Post-migration Jira comments / attachments copy")
    parser.add_argument("--config",default="config.local.json")
    parser.add_argument("--jira-project",required=True)
    parser.add_argument("--kind",choices=("set","case"),required=True)
    parser.add_argument("--tracker-id",type=int,required=True)
    parser.add_argument("--state-dir",default=".migration/test-case-set-batch")
    parser.add_argument("--limit",type=int,default=1,help="Maximum source Items; 0=all")
    parser.add_argument("--max-attachment-bytes",type=int,default=20*1024*1024)
    parser.add_argument("--include-comments",action="store_true")
    parser.add_argument("--include-attachments",action="store_true")
    parser.add_argument("--prompt-auth",action="store_true")
    parser.add_argument("--execute",action="store_true")
    parser.add_argument("--confirm-project")
    args=parser.parse_args(argv)
    if args.tracker_id<=0 or args.limit<0 or args.max_attachment_bytes<=0:
        parser.error("Invalid tracker/limit/file-size input")
    if not (args.include_comments or args.include_attachments):
        parser.error("Select --include-comments or --include-attachments")
    if args.execute and args.confirm_project!=args.jira_project:
        parser.error("For writes, --confirm-project must equal --jira-project")
    cfg=json.loads(Path(args.config).read_text(encoding="utf-8-sig"))
    key=stem(cfg["jira"]["url"],cfg["codebeamer"]["url"],args.jira_project,args.kind,args.tracker_id)
    root=Path(args.state_dir)
    manifest=read_children(root/("children-"+key+".jsonl"))
    journal=root/("imported-children-"+key+".jsonl")
    previous=read_journal(journal)
    jira=make_http(cfg["jira"],root/"jira-children-requests.jsonl",prompt_auth=args.prompt_auth)
    cb=make_http(cfg["codebeamer"],root/"cb-children-requests.jsonl",prompt_auth=args.prompt_auth)
    done=skipped=inspected=0
    for key_source,record in manifest.items():
        if args.limit and inspected>=args.limit:
            break
        inspected+=1
        for category,enabled,children in (
            ("comment",args.include_comments,record["comments"]),
            ("attachment",args.include_attachments,record["attachments"])):
            if not enabled:
                continue
            for child in children:
                identity=":".join((str(record["cb"]),category,str(child["id"])))
                old=previous.get(identity)
                if old:
                    if old["status"]=="VERIFIED":
                        skipped+=1
                        continue
                    raise ApiError("Unreconciled prior child write: "+identity)
                if not args.execute:
                    print(json.dumps({"jira":key_source,"child_type":category,
                                      "source_id":child["id"],"status":"READY"}),flush=True)
                    continue
                # Source validation before recording a possible write.
                if category=="comment" and not isinstance(child.get("body"),str):
                    raise ApiError("Comment body is not text")
                if category=="attachment" and (type(child.get("size")) is not int or
                      child["size"]>args.max_attachment_bytes):
                    raise ApiError("Attachment exceeds maximum allowed size")
                attachment_bytes = (read_jira_attachment(jira,child,args.max_attachment_bytes)
                                    if category=="attachment" else None)
                append_event(journal,{"identity":identity,"jira":key_source,
                                      "status":"POST_ATTEMPT"})
                try:
                    if category=="comment":
                        created=import_comment(cb,record["cb"],child)
                    else:
                        created=import_attachment(cb,record["cb"],child,attachment_bytes)
                except Exception as exc:
                    append_event(journal,{"identity":identity,"status":"POST_UNCERTAIN_OR_FAILED",
                                          "error_type":type(exc).__name__})
                    raise
                event={"identity":identity,"status":"VERIFIED","cb_child_id":created}
                append_event(journal,event)
                previous[identity]=event
                done+=1
                print(json.dumps({"jira":key_source,"child_type":category,
                                  "source_id":child["id"],"status":"VERIFIED"}),flush=True)
    print(json.dumps({"status":"CHILDREN_ONLY","inspected_items":inspected,
                      "newly_verified_children":done,"skipped_children":skipped,
                      "limitation":"Original CB comment author/timestamps are not restored"},
                     ensure_ascii=False),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
