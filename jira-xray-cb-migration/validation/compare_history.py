from clients.http import ApiError


def revision_ids(history):
    versions = history.get('versions')
    if not isinstance(versions, list):
        raise ApiError('History versions missing')
    if history.get('total', len(versions)) != len(versions):
        raise ApiError('Target history pagination incomplete')
    return {v['itemRevision']['version'] for v in versions}
