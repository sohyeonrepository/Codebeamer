import hashlib
import json
import uuid
from pathlib import Path
from clients.http import ApiError


def transfer(source_http, cb, state, parent_key, item_id, attachment):
    aid = str(attachment['id'])
    filename = attachment['filename']
    if '\r' in filename or '\n' in filename:
        raise ApiError('Invalid filename')
    def upload():
        content = source_http.request('GET', attachment['content'], raw=True)
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
        result = cb.call('attachment_upload', {'itemId': item_id}, body,
                         headers={'Content-Type': 'multipart/form-data; boundary=' + boundary})
        if 'id' not in result:
            raise ApiError('Attachment upload response lacks id; reconcile before retry')
        downloaded = cb.call('attachment_get', {'itemId': item_id, 'attachmentId': result['id']}, raw=True)
        if hashlib.sha256(downloaded).hexdigest() != digest or len(downloaded) != len(content):
            raise ApiError('Uploaded binary hash/size mismatch')
        return {'id': result['id'], 'source_id': aid, 'filename': filename, 'size': len(content), 'sha256': digest}
    return state.once(f'attachment:{parent_key}:{aid}', upload)
