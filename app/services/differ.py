from flask import current_app
from app.models import db
from app.services.matcher import build_match_index, find_best_match


def _load_bom_items(bom_id):
    """Load all items for a BOM."""
    rows = db.query(
        'SELECT * FROM bom_item WHERE bom_id=? ORDER BY line_no', (bom_id,)
    )
    return [dict(r) for r in rows]


def _classify_severity(diff_type, diff_category):
    """Classify severity for a diff."""
    high_types = ('added', 'removed')
    high_cats = ('material', 'reference')
    medium_cats = ('quantity', 'specification', 'alternative')

    if diff_type in high_types and diff_category in high_cats:
        return 'high'
    if diff_category in medium_cats:
        return 'medium'
    if diff_category == 'version':
        return 'low'
    return 'medium'


def _classify_ref_change(refs_a, items_a_by_ref, items_b_by_ref):
    """Classify reference designator changes into 3 sub-types.

    Returns list of diff records.
    """
    diffs = []

    # All refs in B
    refs_b_set = set()
    for item in refs_a:
        for ref in item.get('reference', '').split(','):
            ref = ref.strip()
            if ref:
                refs_b_set.add(ref.upper())

    return diffs


def run_comparison(source_bom_id, target_bom_id, comparison_type='version'):
    """Run BOM comparison between source (A) and target (B).

    Returns task_id.
    """
    items_a = _load_bom_items(source_bom_id)
    items_b = _load_bom_items(target_bom_id)

    index_a = build_match_index(items_a)
    index_b = build_match_index(items_b)

    # Build reference indexes
    ref_a_map = {}  # ref_upper -> list of items
    ref_b_map = {}
    for item in items_a:
        for ref in str(item.get('reference', '')).split(','):
            ref = ref.strip().upper()
            if ref:
                ref_a_map.setdefault(ref, []).append(item)
    for item in items_b:
        for ref in str(item.get('reference', '')).split(','):
            ref = ref.strip().upper()
            if ref:
                ref_b_map.setdefault(ref, []).append(item)

    config = current_app.config
    diff_records = []

    # --- 1. Material-level: items in B not in A (added) ---
    for item_b in items_b:
        pn_b = item_b['part_number'].strip().upper()
        matched_pn, score, matched_items = find_best_match(pn_b, index_a, config['MATCH_THRESHOLD_MEDIUM'])

        if matched_pn is None:
            diff_records.append({
                'diff_type': 'added',
                'diff_category': 'material',
                'severity': 'high',
                'part_number_b': item_b['part_number'],
                'part_name_b': item_b['part_name'],
                'field_name': 'part_number',
                'new_value': item_b['part_number'],
                'reference_b': item_b['reference'],
                'quantity_b': item_b['quantity'],
                'match_confidence': 0,
            })

    # --- 2. Material-level: items in A not in B (removed) ---
    for item_a in items_a:
        pn_a = item_a['part_number'].strip().upper()
        matched_pn, score, matched_items = find_best_match(pn_a, index_b, config['MATCH_THRESHOLD_MEDIUM'])

        if matched_pn is None:
            diff_records.append({
                'diff_type': 'removed',
                'diff_category': 'material',
                'severity': 'high',
                'part_number_a': item_a['part_number'],
                'part_name_a': item_a['part_name'],
                'field_name': 'part_number',
                'old_value': item_a['part_number'],
                'reference_a': item_a['reference'],
                'quantity_a': item_a['quantity'],
                'match_confidence': 0,
            })

    # --- 3. Matched items: check field-level diffs ---
    comparable_fields = ['part_name', 'specification', 'quantity', 'unit',
                         'version', 'manufacturer', 'mpn']

    matched_set_a = set()
    matched_set_b = set()

    for item_a in items_a:
        pn_a = item_a['part_number'].strip().upper()
        matched_pn, score, matched_items_b = find_best_match(pn_a, index_b, config['MATCH_THRESHOLD_MEDIUM'])

        if matched_pn is not None and score >= config['MATCH_THRESHOLD_HIGH']:
            matched_set_a.add(pn_a)
            matched_set_b.add(matched_pn)

            item_b = matched_items_b[0]  # take first match

            for field in comparable_fields:
                val_a = str(item_a.get(field, '')).strip()
                val_b = str(item_b.get(field, '')).strip()

                if val_a and val_b and val_a.upper() != val_b.upper():
                    cat = 'quantity' if field == 'quantity' else field
                    severity = _classify_severity('modified', cat)
                    diff_records.append({
                        'diff_type': 'modified',
                        'diff_category': cat,
                        'severity': severity,
                        'part_number_a': item_a['part_number'],
                        'part_number_b': item_b['part_number'],
                        'part_name_a': item_a['part_name'],
                        'part_name_b': item_b['part_name'],
                        'field_name': field,
                        'old_value': val_a,
                        'new_value': val_b,
                        'quantity_a': item_a['quantity'],
                        'quantity_b': item_b['quantity'],
                        'reference_a': item_a['reference'],
                        'reference_b': item_b['reference'],
                        'match_confidence': score,
                    })

    # --- 4. Reference designator changes ---
    all_refs = set(ref_a_map.keys()) | set(ref_b_map.keys())
    for ref in all_refs:
        items_in_a = ref_a_map.get(ref, [])
        items_in_b = ref_b_map.get(ref, [])
        pns_a = set(i['part_number'].strip().upper() for i in items_in_a)
        pns_b = set(i['part_number'].strip().upper() for i in items_in_b)

        if pns_a and not pns_b:
            # Ref removed
            for item in items_in_a:
                diff_records.append({
                    'diff_type': 'removed',
                    'diff_category': 'reference',
                    'severity': 'high',
                    'part_number_a': item['part_number'],
                    'part_name_a': item['part_name'],
                    'field_name': 'reference',
                    'old_value': ref,
                    'reference_a': ref,
                    'quantity_a': item['quantity'],
                    'match_confidence': 100,
                })
        elif pns_b and not pns_a:
            # Ref added
            for item in items_in_b:
                diff_records.append({
                    'diff_type': 'added',
                    'diff_category': 'reference',
                    'severity': 'high',
                    'part_number_b': item['part_number'],
                    'part_name_b': item['part_name'],
                    'field_name': 'reference',
                    'new_value': ref,
                    'reference_b': ref,
                    'quantity_b': item['quantity'],
                    'match_confidence': 100,
                })
        elif pns_a != pns_b:
            # Ref changed - classify sub-type
            old_pn = list(pns_a)[0] if pns_a else ''
            new_pn = list(pns_b)[0] if pns_b else ''
            new_in_a = new_pn in index_a

            sub_type = 'normal_replace' if new_in_a else 'new_material'
            diff_records.append({
                'diff_type': 'modified',
                'diff_category': 'reference',
                'severity': 'high',
                'part_number_a': items_in_a[0]['part_number'] if items_in_a else '',
                'part_number_b': items_in_b[0]['part_number'] if items_in_b else '',
                'part_name_a': items_in_a[0]['part_name'] if items_in_a else '',
                'part_name_b': items_in_b[0]['part_name'] if items_in_b else '',
                'field_name': f'reference_{sub_type}',
                'old_value': f'{ref} -> {old_pn}',
                'new_value': f'{ref} -> {new_pn}',
                'reference_a': ref,
                'reference_b': ref,
                'match_confidence': 100,
            })

    # --- 5. Material consolidation detection ---
    pn_b_refs = {}
    for item_b in items_b:
        for ref in str(item_b.get('reference', '')).split(','):
            ref = ref.strip().upper()
            if ref:
                pn_b_refs.setdefault(item_b['part_number'].strip().upper(), set()).add(ref)

    pn_a_refs = {}
    for item_a in items_a:
        for ref in str(item_a.get('reference', '')).split(','):
            ref = ref.strip().upper()
            if ref:
                pn_a_refs.setdefault(item_a['part_number'].strip().upper(), set()).add(ref)

    # Detect N:1 consolidation (multiple A materials -> single B material)
    for pn_b, refs_b in pn_b_refs.items():
        source_pns = set()
        for ref in refs_b:
            if ref in ref_a_map:
                for item_a in ref_a_map[ref]:
                    source_pns.add(item_a['part_number'].strip().upper())
        if len(source_pns) > 1:
            diff_records.append({
                'diff_type': 'modified',
                'diff_category': 'consolidation',
                'severity': 'high',
                'part_number_b': pn_b,
                'field_name': 'consolidation',
                'old_value': ', '.join(sorted(source_pns)),
                'new_value': pn_b,
                'reference_b': ', '.join(sorted(refs_b)),
                'match_confidence': 100,
            })

    # --- 6. Parent / hierarchy change detection ---
    # Same part_number but different parent or level
    matched_pairs = []  # [(item_a, item_b, score)]
    for item_a in items_a:
        pn_a = item_a['part_number'].strip().upper()
        matched_pn, score, matched_items_b = find_best_match(pn_a, index_b, config['MATCH_THRESHOLD_HIGH'])
        if matched_pn is not None and score >= config['MATCH_THRESHOLD_HIGH']:
            for ib in matched_items_b:
                if ib['part_number'].strip().upper() == matched_pn:
                    matched_pairs.append((item_a, ib, score))
                    break

    for item_a, item_b, score in matched_pairs:
        parent_a = str(item_a.get('parent_pn', '')).strip()
        parent_b = str(item_b.get('parent_pn', '')).strip()
        level_a = item_a.get('level', 0)
        level_b = item_b.get('level', 0)

        # Parent change: same material attached under different parent
        if parent_a and parent_b and parent_a != parent_b:
            diff_records.append({
                'diff_type': 'modified',
                'diff_category': 'hierarchy',
                'severity': 'high',
                'part_number_a': item_a['part_number'],
                'part_number_b': item_b['part_number'],
                'part_name_a': item_a['part_name'],
                'part_name_b': item_b['part_name'],
                'field_name': 'parent_pn',
                'old_value': parent_a,
                'new_value': parent_b,
                'quantity_a': item_a['quantity'],
                'quantity_b': item_b['quantity'],
                'match_confidence': score,
            })

        # Level change
        if level_a and level_b and int(level_a) != int(level_b):
            diff_records.append({
                'diff_type': 'modified',
                'diff_category': 'hierarchy',
                'severity': 'medium',
                'part_number_a': item_a['part_number'],
                'part_number_b': item_b['part_number'],
                'part_name_a': item_a['part_name'],
                'part_name_b': item_b['part_name'],
                'field_name': 'level',
                'old_value': str(int(level_a)),
                'new_value': str(int(level_b)),
                'quantity_a': item_a['quantity'],
                'quantity_b': item_b['quantity'],
                'match_confidence': score,
            })

    # --- Save results ---
    source_bom = db.query_one('SELECT bom_name FROM bom_header WHERE id=?', (source_bom_id,))
    target_bom = db.query_one('SELECT bom_name FROM bom_header WHERE id=?', (target_bom_id,))
    task_name = f"{source_bom['bom_name']} vs {target_bom['bom_name']}"

    cursor = db.execute('''
        INSERT INTO comparison_task (task_name, source_bom_id, target_bom_id, comparison_type, status)
        VALUES (?, ?, ?, ?, ?)
    ''', (task_name, source_bom_id, target_bom_id, comparison_type, 'completed'))

    task_id = cursor.lastrowid

    for rec in diff_records:
        db.execute('''
            INSERT INTO comparison_result (
                task_id, diff_type, diff_category, severity,
                part_number_a, part_number_b, part_name_a, part_name_b,
                field_name, old_value, new_value,
                reference_a, reference_b, quantity_a, quantity_b,
                match_confidence
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ''', (
            task_id, rec.get('diff_type', ''), rec.get('diff_category', ''),
            rec.get('severity', 'medium'),
            rec.get('part_number_a', ''), rec.get('part_number_b', ''),
            rec.get('part_name_a', ''), rec.get('part_name_b', ''),
            rec.get('field_name', ''), rec.get('old_value', ''),
            rec.get('new_value', ''),
            rec.get('reference_a', ''), rec.get('reference_b', ''),
            rec.get('quantity_a', 0), rec.get('quantity_b', 0),
            rec.get('match_confidence', 100),
        ))

    db.execute('UPDATE comparison_task SET completed_at=CURRENT_TIMESTAMP WHERE id=?', (task_id,))

    return task_id
