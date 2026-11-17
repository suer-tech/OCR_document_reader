"""Evaluate RTK3 regular expressions and arithmetic against explicit label masks.

Usage: python scripts/evaluate_rtk3_regex.py PREPARED_ROOT OUTPUT_JSON [PARTITION]
PARTITION defaults to all names in regex-splits.json. No model or network calls.
Run against a private prepared directory; never commit document contents.
"""
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys
from unittest.mock import patch

from ocr_platform.services.rtk3_evidence import compact, money
from ocr_platform.services.rtk3_regex_candidates import extract_candidates
from ocr_platform.services.rtk3_regex_jev import _claim_key, _claim_values, _fixed_claims
from ocr_platform.services.rtk3_requisites import identity


def evaluate(root, selected):
    counts, examples = defaultdict(Counter), defaultdict(lambda: defaultdict(list))
    absence_counts, absence_examples = defaultdict(Counter), defaultdict(list)
    coverage = Counter()
    docs = 0
    for target in sorted(root.glob("*/document_labels.json")):
        sample = target.parent
        if sample.name not in selected:
            continue
        text = json.loads((sample / "ocr.json").read_text(encoding="utf-8-sig"))["raw_text"]
        labels = json.loads(target.read_text(encoding="utf-8-sig"))["fields"]
        candidates = extract_candidates(text)
        predicted = {name: candidate.value for name, candidate in candidates.fields.items()}
        patch = {_claim_key(i, name): value for i, claim in enumerate(candidates.claims)
                 for name, value in _claim_values(claim).items()}
        all_fields = _fixed_claims(candidates.claims, {}, patch, text)
        predicted.update({name: info["value"] for name, info in all_fields.items()})
        docs += 1

        def record(name, expected, actual):
            if expected is None:
                return
            coverage[name] += actual is not None
            if actual is None:
                outcome = "missing"
            elif name.startswith("claims[].") and name.split(".")[1] in {
                    "principal_debt", "financial_sanctions", "pau_only_amount", "claimed_amount", "total_amount"
            } or name == "total_claimed_amount":
                outcome = "match" if money(expected) == money(actual) else "different"
            elif name == "creditor":
                outcome = "match" if identity(expected) == identity(actual) else "different"
            elif name == "grounds_ocr":
                outcome = "match" if compact(expected) == compact(actual) else (
                    "similar" if compact(actual) in compact(expected) or compact(expected) in compact(actual) else "different")
            else:
                outcome = "match" if expected == actual else "different"
            counts[name][outcome] += 1
            if outcome in {"different", "missing"}:
                examples[name][outcome].append({"sample": sample.name, "expected": expected, "actual": actual})

        for name, item in labels.items():
            if name in {"kad_publication_date", "processing_started_at", "claims"}:
                continue
            if item['value'] is None and item.get('status') == 'not_present_in_document':
                actual = predicted.get(name)
                absence_counts[name]['correctly_empty' if actual is None else 'unexpected_value'] += 1
                if actual is not None:
                    absence_examples[name].append({'sample': sample.name, 'actual': actual})
            record(name, item["value"], predicted.get(name))
        if "claims" in labels:
            expected = labels["claims"]["value"]
            actual = predicted["claims"]
            record("claim_queues", sorted(row["priority_queue"] for row in expected if row["priority_queue"]),
                   sorted(row["priority_queue"] for row in actual if row["priority_queue"]) if actual else None)
            actual_by_queue = {row["priority_queue"]: row for row in actual}
            for row in expected:
                aligned = actual_by_queue.get(row["priority_queue"], {})
                for name, value in row.items():
                    record("claims[]." + name, value, aligned.get(name))
        for name, value in predicted.items():
            if name not in labels and value not in (None, []):
                coverage[name + " (unlabelled)"] += 1
    return {"documents": docs, "counts": counts, "coverage": coverage, "examples": examples,
            "absence_counts": absence_counts, "absence_examples": absence_examples}


if __name__ == "__main__":
    root = Path(sys.argv[1])
    manifest = json.loads((root / 'regex-splits.json').read_text(encoding='utf-8'))['splits']
    partition = sys.argv[3] if len(sys.argv) > 3 else 'all'
    selected = set().union(*(set(v) for v in manifest.values())) if partition == 'all' else set(manifest[partition])
    with patch('socket.socket.connect', side_effect=RuntimeError('Network disabled in regex-only evaluation')):
        report = evaluate(root, selected)
    if report['documents'] != len(selected):
        raise ValueError('Some selected samples are missing prepared document_labels.json')
    report['partition'] = partition
    print(report["documents"], "documents")
    for k, v in sorted(report["counts"].items()):
        print(k, dict(v))
    Path(sys.argv[2]).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
