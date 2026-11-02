import hashlib
import json
import uuid
from pathlib import Path
from clients.http import ApiError


def transfer(source_http, cb, state, parent_key, item_id, attachment, reconcile=False):
    aid = str(attachment['id'])
    filename = attachment['filename']
    if '\r' in filename or '\n' in filename:
        raise ApiError('Invalid filename')
    prefetched = None
    def upload():
        content = prefetched if prefetched is not None else source_http.request('GET', attachment['content'], raw=True)
        if len(content) != attachment['size']:
            raise ApiError('Source attachment size mismatch')
        digest = hashlib.sha256(content).hexdigest()
        directory = state.root / 'binaries'
        directory.mkdir(exist_ok=True)
        (directory / digest).write_bytes(content)
        boundary = 'migration' + uuid.uuid4().hex
        part = cb.operation('attachment_upload').get('part_name', 'file')
        if not part.isidentifier():
            raise ApiError('Invalid multipart part name')
        # filename* retains Unicode filenames; quote escaping protects the fallback.
        from urllib.parse import quote
        safe_name = filename.replace('\\', '_').replace('"', '_')
        header = f'--{boundary}\r\nContent-Disposition: form-data; name="{part}"; filename="{safe_name}"; filename*=UTF-8\'\'{quote(filename)}\r\nContent-Type: application/octet-stream\r\n\r\n'
        body = header.encode() + content + f'\r\n--{boundary}--\r\n'.encode()
        result = cb.call('attachment_upload', {'itemId': item_id, 'sourceId': aid, 'sha256': digest}, body,
                         headers={'Content-Type': 'multipart/form-data; boundary=' + boundary})
        if 'id' not in result:
            raise ApiError('Attachment upload response lacks id; reconcile before retry')
        downloaded = cb.call('attachment_get', {'itemId': item_id, 'attachmentId': result['id']}, raw=True)
        if hashlib.sha256(downloaded).hexdigest() != digest or len(downloaded) != len(content):
            raise ApiError('Uploaded binary hash/size mismatch')
        return {'id': result['id'], 'source_id': aid, 'filename': filename, 'size': len(content), 'sha256': digest}
    identity = f'attachment:{parent_key}:{aid}'
    if reconcile:
        # Source-read failures occur before marking any target write pending.
        content = source_http.request('GET', attachment['content'], raw=True)
        if len(content) != attachment['size']:
            raise ApiError('Source attachment size mismatch')
        digest = hashlib.sha256(content).hexdigest()
        prefetched = content
        found = cb.call('attachment_lookup', {'itemId': item_id, 'sourceId': aid, 'sha256': digest})
        if not isinstance(found, list) or len(found) > 1:
            raise ApiError('Ambiguous attachment external-ID lookup')
        previous = state.get(identity)
        if found:
            if previous and previous[0] == 'done' and previous[1]['id'] != found[0]['id']:
                raise ApiError('Attachment lookup disagrees with saved ID')
            result = {'id': found[0]['id'], 'source_id': aid, 'filename': filename,
                      'size': len(content), 'sha256': digest}
            downloaded = cb.call('attachment_get', {'itemId': item_id, 'attachmentId': result['id']}, raw=True)
            if downloaded != content:
                raise ApiError('Existing attachment content mismatch')
            state.mark(identity, 'done', result)
            return result
        if previous:
            raise ApiError('Uncertain/missing attachment: reconcile server state before retry')
    return state.once(identity, upload)
