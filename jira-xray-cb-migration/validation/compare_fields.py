def compare_fields(expected, actual):
    return [{'field': k, 'reason': 'value mismatch or missing'} for k, value in expected.items()
            if k not in actual or actual[k] != value]
