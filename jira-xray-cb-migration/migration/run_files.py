"""Find Run/Step attachments including evidence removed in historical revisions."""
from clients.http import ApiError


CONTAINERS = {'attachment', 'attachments', 'evidence', 'evidences'}


def inventory(data):
    result = []
    def visit(node):
        if isinstance(node, list):
            for value in node:
                visit(value)
        elif isinstance(node, dict):
            for key, value in node.items():
                if key.casefold() in CONTAINERS:
                    if value is None:
                        continue
                    rows = value if isinstance(value, list) else [value]
                    for entry in rows:
                        if not isinstance(entry, dict):
                            raise ApiError('Attachment/Evidence metadata needs a server adapter')
                        aliases = {'id': ('id', 'attachmentId', 'evidenceId'),
                                   'filename': ('filename', 'fileName', 'name'),
                                   'size': ('size', 'fileSize'),
                                   'content': ('content', 'downloadUrl', 'url')}
                        metadata = {target: next((entry[a] for a in names if a in entry), None) for target, names in aliases.items()}
                        if any(v is None for v in metadata.values()):
                            raise ApiError('Attachment metadata lacks id/filename/size/download URL; configure explicit attachment_lists/evidence_lists')
                        if type(metadata['size']) is not int or metadata['size'] < 0:
                            raise ApiError('Attachment size must be a nonnegative integer')
                        if not isinstance(metadata['content'], str):
                            raise ApiError('Attachment download URL must be a string')
                        result.append(metadata)
                else:
                    visit(value)
    visit(data)
    return result
