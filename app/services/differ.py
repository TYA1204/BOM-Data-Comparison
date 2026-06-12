from flask import current_app
from app.models import db


def _load_bom_items(bom_id):
    """Load all items for a BOM."""
    rows = db.query(
        'SELECT * FROM bom_item WHERE bom_id=? ORDER BY line_no', (bom_id,)
    )
    return [dict(r) for r in rows]


def _build_exact_index(items):
    """Build exact-match lookup: upper(part_number) -> list of items."""
    index = {}
    for item in items:
        pn = item['part_number'].strip().upper()
        if pn not in index:
            index[pn] = []
        index[pn].append(item)
    return index


def _classify_severity(diff_type, diff_category):
    """Classify severity for a diff.

    Simplified: only 3 diff categories remain.
      - material add/remove → high
      - quantity/unit change   → medium
    """
    if diff_type in ('added', 'removed'):
        return 'high'
    if diff_category in ('quantity', 'unit'):
        return 'medium'
    if diff_category == 'structure':
        return 'medium'
    if diff_category == 'version':
        return 'low'
    return 'medium'


def run_comparison(source_bom_id, target_bom_id, comparison_type='version',
                   compare_mode='components_only', selected_components=None,
                   exclude_parents=None, exclude_leaves=False,
                   skip_pns=None):
    """Run BOM comparison between source (A) and target (B).

    Uses EXACT part-number matching only (no fuzzy matching).
    Compares 3 dimensions:
      1. Added materials   — PN exists in B but not in A
      2. Removed materials — PN exists in A but not in B
      3. Field changes     — same PN + same parent, different quantity/unit

    Args:
        source_bom_id: 基准BOM ID (baseline)
        target_bom_id: 目标BOM ID (comparison target)
        comparison_type: 'version' or 'cross_model'
        compare_mode: 'components_only' (仅对比组件本身+直接子件)
                      or 'include_children' (对比组件及整个子树)
        selected_components: dict {'source': [pn1,pn2], 'target': [pn3,pn4]}
        exclude_parents: list of PNs to exclude (recursively excludes all descendants)

    Returns task_id.
    """
    if selected_components is None:
        selected_components = {}

    exclude_parents = exclude_parents or []
    exclude_parents = set(p.strip().upper() for p in exclude_parents if p and p.strip())

    items_a = _load_bom_items(source_bom_id)
    items_b = _load_bom_items(target_bom_id)

    # --- Filter items by selected components ---
    src_pns = set(selected_components.get('source', []) or [])
    tgt_pns = set(selected_components.get('target', []) or [])

    if src_pns or tgt_pns:
        if compare_mode == 'components_only':
            items_a = [it for it in items_a
                       if it['part_number'] in src_pns
                       or (it['parent_pn'] in src_pns and it['parent_pn'])]
            items_b = [it for it in items_b
                       if it['part_number'] in tgt_pns
                       or (it['parent_pn'] in tgt_pns and it['parent_pn'])]
        else:
            def _collect_subtree(items, root_pns):
                children_map = {}
                for it in items:
                    ppn = it['parent_pn'] or ''
                    if ppn:
                        children_map.setdefault(ppn, []).append(it['part_number'])

                reachable = set(root_pns)
                queue = list(root_pns)
                while queue:
                    pn = queue.pop(0)
                    for child in children_map.get(pn, []):
                        if child not in reachable:
                            reachable.add(child)
                            queue.append(child)
                return [it for it in items
                        if it['part_number'] in reachable
                        or it['part_number'] in root_pns]

            items_a = _collect_subtree(items_a, src_pns)
            items_b = _collect_subtree(items_b, tgt_pns)

    if not items_a and not items_b:
        raise ValueError('选择的组件下没有可对比的物料数据')

    # --- Exclude specific parents (recursively exclude all descendants) ---
    if exclude_parents:
        def _collect_all_descendants(items, root_pns):
            """Collect all PNs that are descendants of any root PN."""
            root_set = set(p.strip().upper() for p in root_pns)
            children_map = {}
            for it in items:
                ppn = (it['parent_pn'] or '').strip().upper()
                if ppn:
                    children_map.setdefault(ppn, []).append(it['part_number'].strip().upper())

            reachable = set(root_set)
            queue = list(root_set)
            while queue:
                pn = queue.pop(0)
                for child in children_map.get(pn, []):
                    if child not in reachable:
                        reachable.add(child)
                        queue.append(child)
            return reachable

        # Find which exclude PNs exist in each BOM
        exclude_a = set()
        for it in items_a:
            if it['part_number'].strip().upper() in exclude_parents:
                exclude_a.add(it['part_number'].strip().upper())
        exclude_b = set()
        for it in items_b:
            if it['part_number'].strip().upper() in exclude_parents:
                exclude_b.add(it['part_number'].strip().upper())

        # Collect all descendants to exclude
        excluded_pns_a = _collect_all_descendants(items_a, exclude_a) | exclude_a
        excluded_pns_b = _collect_all_descendants(items_b, exclude_b) | exclude_b

        items_a = [it for it in items_a
                   if it['part_number'].strip().upper() not in excluded_pns_a]
        items_b = [it for it in items_b
                   if it['part_number'].strip().upper() not in excluded_pns_b]

    if not items_a and not items_b:
        raise ValueError('排除指定组件后无剩余可对比物料')

    # --- Exclude leaf nodes (items without children, i.e. not assemblies) ---
    if exclude_leaves:
        parents_a = set((it['parent_pn'] or '').strip().upper() for it in items_a if it['parent_pn'])
        parents_b = set((it['parent_pn'] or '').strip().upper() for it in items_b if it['parent_pn'])
        items_a = [it for it in items_a if it['part_number'].strip().upper() in parents_a]
        items_b = [it for it in items_b if it['part_number'].strip().upper() in parents_b]

    if not items_a and not items_b:
        raise ValueError('排除叶子节点后无剩余可对比物料')

    # Build exact-match indexes (PN -> list of items)
    index_a = _build_exact_index(items_a)
    index_b = _build_exact_index(items_b)

    diff_records = []

    # =================================================================
    # Step 1 — Added materials: PN in B but not in A (exact match)
    # =================================================================
    for item_b in items_b:
        pn_b = item_b['part_number'].strip().upper()
        if pn_b not in index_a:
            diff_records.append({
                'diff_type': 'added',
                'diff_category': 'material',
                'severity': 'high',
                'part_number_b': item_b['part_number'],
                'part_name_b': item_b['part_name'],
                'field_name': 'part_number',
                'new_value': item_b['part_number'],
                'reference_b': item_b.get('reference', ''),
                'quantity_b': item_b['quantity'],
                'match_confidence': 0,
                'parent_pn_b': item_b.get('parent_pn', ''),
            })

    # =================================================================
    # Step 2 — Removed materials: PN in A but not in B (exact match)
    # =================================================================
    for item_a in items_a:
        pn_a = item_a['part_number'].strip().upper()
        if pn_a not in index_b:
            diff_records.append({
                'diff_type': 'removed',
                'diff_category': 'material',
                'severity': 'high',
                'part_number_a': item_a['part_number'],
                'part_name_a': item_a['part_name'],
                'field_name': 'part_number',
                'old_value': item_a['part_number'],
                'reference_a': item_a.get('reference', ''),
                'quantity_a': item_a['quantity'],
                'match_confidence': 0,
                'parent_pn_a': item_a.get('parent_pn', ''),
            })

    # =================================================================
    # Step 3 — Field changes: same PN + same parent, different quantity/unit
    # =================================================================
    def _pick_by_parent(candidates, target_parent):
        target = str(target_parent or '').strip()
        for cand in candidates:
            if str(cand.get('parent_pn', '')).strip() == target:
                return cand
        return None

    for item_a in items_a:
        pn_a = item_a['part_number'].strip().upper()
        if pn_a not in index_b:
            continue  # Already reported as "removed" in Step 2

        matched_items_b = index_b[pn_a]
        item_b = _pick_by_parent(matched_items_b, item_a.get('parent_pn', ''))
        if item_b is None:
            continue  # Same PN exists but under different parent — skip (handled by Step 3c)

        # Quantity change
        qty_a = float(item_a.get('quantity', 0) or 0)
        qty_b = float(item_b.get('quantity', 0) or 0)

        if qty_a != qty_b:
            diff_records.append({
                'diff_type': 'modified',
                'diff_category': 'quantity',
                'severity': 'medium',
                'part_number_a': item_a['part_number'],
                'part_number_b': item_b['part_number'],
                'part_name_a': item_a['part_name'],
                'part_name_b': item_b['part_name'],
                'field_name': 'quantity',
                'old_value': str(qty_a),
                'new_value': str(qty_b),
                'quantity_a': item_a['quantity'],
                'quantity_b': item_b['quantity'],
                'reference_a': item_a.get('reference', ''),
                'reference_b': item_b.get('reference', ''),
                'match_confidence': 100,
                'parent_pn_a': item_a.get('parent_pn', ''),
                'parent_pn_b': item_b.get('parent_pn', ''),
            })

        # Unit change
        unit_a = str(item_a.get('unit', '')).strip().upper()
        unit_b = str(item_b.get('unit', '')).strip().upper()

        if unit_a and unit_b and unit_a != unit_b:
            diff_records.append({
                'diff_type': 'modified',
                'diff_category': 'unit',
                'severity': 'medium',
                'part_number_a': item_a['part_number'],
                'part_number_b': item_b['part_number'],
                'part_name_a': item_a['part_name'],
                'part_name_b': item_b['part_name'],
                'field_name': 'unit',
                'old_value': item_a.get('unit', ''),
                'new_value': item_b.get('unit', ''),
                'quantity_a': item_a['quantity'],
                'quantity_b': item_b['quantity'],
                'reference_a': item_a.get('reference', ''),
                'reference_b': item_b.get('reference', ''),
                'match_confidence': 100,
                'parent_pn_a': item_a.get('parent_pn', ''),
                'parent_pn_b': item_b.get('parent_pn', ''),
            })

    # =================================================================
    # Step 3b — Version changes (版本比对 only):
    #   Same PN + same parent, different version/revision
    # =================================================================
    if comparison_type == 'version':
        for item_a in items_a:
            pn_a = item_a['part_number'].strip().upper()
            if pn_a not in index_b:
                continue
            matched_items_b = index_b[pn_a]
            item_b = _pick_by_parent(matched_items_b, item_a.get('parent_pn', ''))
            if item_b is None:
                continue

            ver_a = str(item_a.get('version', '')).strip()
            ver_b = str(item_b.get('version', '')).strip()

            if ver_a and ver_b and ver_a != ver_b:
                diff_records.append({
                    'diff_type': 'modified',
                    'diff_category': 'version',
                    'severity': 'low',
                    'part_number_a': item_a['part_number'],
                    'part_number_b': item_b['part_number'],
                    'part_name_a': item_a['part_name'],
                    'part_name_b': item_b['part_name'],
                    'field_name': 'version',
                    'old_value': ver_a,
                    'new_value': ver_b,
                    'quantity_a': item_a['quantity'],
                    'quantity_b': item_b['quantity'],
                    'reference_a': item_a.get('reference', ''),
                    'reference_b': item_b.get('reference', ''),
                    'match_confidence': 100,
                    'parent_pn_a': item_a.get('parent_pn', ''),
                    'parent_pn_b': item_b.get('parent_pn', ''),
                })

    # =================================================================
    # Step 3c — Structure changes (跨机型比对 only):
    #   Same PN exists in both BOMs but under different parent assembly
    # =================================================================
    if comparison_type == 'cross_model':
        for item_a in items_a:
            pn_a = item_a['part_number'].strip().upper()
            if pn_a not in index_b:
                continue
            parent_a = str(item_a.get('parent_pn', '')).strip().upper()
            if not parent_a:
                continue
            matched_items_b = index_b[pn_a]
            for item_b in matched_items_b:
                parent_b = str(item_b.get('parent_pn', '')).strip().upper()
                if parent_b and parent_a != parent_b:
                    diff_records.append({
                        'diff_type': 'modified',
                        'diff_category': 'structure',
                        'severity': 'medium',
                        'part_number_a': item_a['part_number'],
                        'part_number_b': item_b['part_number'],
                        'part_name_a': item_a['part_name'],
                        'part_name_b': item_b['part_name'],
                        'field_name': 'parent_pn',
                        'old_value': item_a.get('parent_pn', ''),
                        'new_value': item_b.get('parent_pn', ''),
                        'quantity_a': item_a['quantity'],
                        'quantity_b': item_b['quantity'],
                        'reference_a': item_a.get('reference', ''),
                        'reference_b': item_b.get('reference', ''),
                        'match_confidence': 100,
                        'parent_pn_a': item_a.get('parent_pn', ''),
                        'parent_pn_b': item_b.get('parent_pn', ''),
                    })

    # =================================================================
    # Post-filter: skip specified PNs from results (exact match only)
    # =================================================================
    if skip_pns:
        skip_set = set(p.strip().upper() for p in skip_pns if p and p.strip())
        diff_records = [r for r in diff_records
                        if r.get('part_number_a', '').strip().upper() not in skip_set
                        and r.get('part_number_b', '').strip().upper() not in skip_set]

    # =================================================================
    # Save results to database
    # =================================================================
    source_bom = db.query_one('SELECT bom_name FROM bom_header WHERE id=?', (source_bom_id,))
    target_bom = db.query_one('SELECT bom_name FROM bom_header WHERE id=?', (target_bom_id,))
    type_label = '版本比对' if comparison_type == 'version' else '跨机型比对'
    task_name = f"[{type_label}] {source_bom['bom_name']} vs {target_bom['bom_name']}"
    if selected_components and (selected_components.get('source') or selected_components.get('target')):
        filters = []
        if selected_components.get('source'):
            filters.append(f"基准BOM {len(selected_components['source'])}个组件")
        if selected_components.get('target'):
            filters.append(f"目标BOM {len(selected_components['target'])}个组件")
        mode_tag = '组件级' if compare_mode == 'components_only' else '含子件'
        task_name = f"{task_name} [{mode_tag}] {'&'.join(filters)}"

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
                match_confidence, parent_pn_a, parent_pn_b
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
            rec.get('parent_pn_a', ''), rec.get('parent_pn_b', ''),
        ))

    db.execute('UPDATE comparison_task SET completed_at=CURRENT_TIMESTAMP WHERE id=?', (task_id,))

    return task_id
