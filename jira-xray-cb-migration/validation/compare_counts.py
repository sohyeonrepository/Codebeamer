def compare_counts(source, target):
    return {key: {'source': count, 'target': target.get(key, 0)} for key, count in source.items()
            if count != target.get(key, 0)}
