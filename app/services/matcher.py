from rapidfuzz import fuzz


def match_score(pn_a, pn_b):
    """Calculate match score between two part numbers."""
    a = str(pn_a).strip().upper()
    b = str(pn_b).strip().upper()
    if not a or not b:
        return 0
    if a == b:
        return 100
    return fuzz.ratio(a, b)


def build_match_index(items_by_pn):
    """Build a lookup: part_number -> list of (bom_id, item_id, pn, name).

    items_by_pn: list of dict with keys: id, part_number, part_name, reference, quantity, ...
    """
    index = {}
    for item in items_by_pn:
        pn = item['part_number'].strip().upper()
        if pn not in index:
            index[pn] = []
        index[pn].append(item)
    return index


def find_best_match(pn, match_index, threshold=80):
    """Find best matching part number in index.

    Returns (matched_pn, score, items) or (None, 0, None).
    """
    pn_upper = str(pn).strip().upper()

    # Exact match first
    if pn_upper in match_index:
        return pn_upper, 100, match_index[pn_upper]

    # Fuzzy match
    best_pn = None
    best_score = 0
    best_items = None

    for candidate_pn, items in match_index.items():
        score = fuzz.ratio(pn_upper, candidate_pn)
        if score > best_score:
            best_score = score
            best_pn = candidate_pn
            best_items = items

    if best_score >= threshold:
        return best_pn, best_score, best_items

    return None, best_score, None
