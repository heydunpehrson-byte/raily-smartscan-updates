"""Conservative application of confirmed, enabled classification rules."""
import json
import re


def apply_rules(conn, result, filename):
    result = dict(result)
    text = re.sub(r'\s+', ' ', result.get('raw_text') or result.get('text') or '').casefold()
    matches = []
    for row in conn.execute('SELECT * FROM learned_rules WHERE enabled=1 ORDER BY id'):
        pattern = re.sub(r'\s+', ' ', row['pattern']).strip().casefold()
        if len(pattern) < 8:
            continue
        candidate = filename.casefold() if row['rule_type'] == 'filename' else text
        if pattern not in candidate:
            continue
        try:
            metadata = json.loads(row['correction_json'])
        except (TypeError, ValueError):
            continue
        category = metadata.get('document_type') if isinstance(metadata, dict) else None
        if category:
            matches.append((row['id'], category))
    if matches:
        result['learned_rule_ids'] = [x[0] for x in matches]
        categories = {x[1] for x in matches}
        if len(categories) == 1:
            result['category'] = next(iter(categories))
            result['review_required'] = float(result.get('ocr_confidence') or 0) < 90
        else:
            result['review_required'] = True
            result['review_reason'] = 'Conflicting learned document types need conductor review.'
    # Person/date/location corrections are not copied onto future documents.
    return result
