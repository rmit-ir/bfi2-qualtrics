#!/usr/bin/env python3
"""Lint a Qualtrics .qsf file against known import invariants.

Checks structural consistency (IDs, cross-references, scoring) per
SCHEMA.md in this directory. Exits non-zero on errors; warnings don't
affect the exit code.

Usage:
    python3 qsf_lint.py <file.qsf> [file2.qsf ...]
"""
import json
import re
import sys

errors = []
warnings = []


def err(msg):
    errors.append(msg)


def warn(msg):
    warnings.append(msg)


def blocks_of(bl_payload):
    """BL.Payload is an array in some exports, a dict keyed '0','1',... in others."""
    if isinstance(bl_payload, list):
        return bl_payload
    if isinstance(bl_payload, dict):
        return list(bl_payload.values())
    return []


def lint(path):
    with open(path, "r", encoding="utf-8") as f:
        try:
            qsf = json.load(f)
        except json.JSONDecodeError as e:
            err(f"{path}: not valid JSON: {e}")
            return

    entry = qsf.get("SurveyEntry")
    elements = qsf.get("SurveyElements")
    if not isinstance(entry, dict) or not isinstance(elements, list):
        err(f"{path}: missing SurveyEntry object or SurveyElements array")
        return

    survey_id = entry.get("SurveyID")
    if not survey_id:
        err(f"{path}: SurveyEntry.SurveyID missing")

    by_type = {}
    for el in elements:
        by_type.setdefault(el.get("Element"), []).append(el)
        if el.get("SurveyID") != survey_id:
            err(f"{path}: {el.get('Element')}/{el.get('PrimaryAttribute')}: "
                f"SurveyID {el.get('SurveyID')!r} != SurveyEntry {survey_id!r}")

    for required in ("BL", "FL"):
        if required not in by_type:
            err(f"{path}: no {required} element")
    for singleton in ("BL", "FL", "SO", "SCO", "QC"):
        if len(by_type.get(singleton, [])) > 1:
            err(f"{path}: {len(by_type[singleton])} {singleton} elements, expected at most 1")

    # --- Blocks ---
    block_ids, question_refs = {}, {}
    default_blocks = 0
    if "BL" in by_type:
        for block in blocks_of(by_type["BL"][0].get("Payload")):
            bid = block.get("ID")
            if not bid:
                err(f"{path}: block without ID: {block.get('Description')!r}")
                continue
            if bid in block_ids:
                err(f"{path}: duplicate block ID {bid}")
            block_ids[bid] = block
            if block.get("Type") == "Default":
                default_blocks += 1
            for be in block.get("BlockElements", []):
                if be.get("Type") == "Question":
                    question_refs.setdefault(be.get("QuestionID"), []).append(bid)
        if default_blocks != 1:
            warn(f"{path}: {default_blocks} Default blocks, expected exactly 1")

    # --- Questions ---
    sq_by_qid = {}
    for el in by_type.get("SQ", []):
        p = el.get("Payload") or {}
        qid = el.get("PrimaryAttribute")
        if qid in sq_by_qid:
            err(f"{path}: duplicate SQ {qid}")
        sq_by_qid[qid] = p
        if p.get("QuestionID") != qid:
            err(f"{path}: SQ {qid}: Payload.QuestionID is {p.get('QuestionID')!r}")
        choices = p.get("Choices") or {}
        order = [str(c) for c in (p.get("ChoiceOrder") or [])]
        missing = [c for c in order if c not in choices]
        if missing:
            err(f"{path}: SQ {qid}: ChoiceOrder references missing choices {missing}")
        answers = p.get("Answers") or {}
        aorder = [str(a) for a in (p.get("AnswerOrder") or [])]
        amissing = [a for a in aorder if a not in answers]
        if amissing:
            err(f"{path}: SQ {qid}: AnswerOrder references missing answers {amissing}")
        if choices:
            max_choice = max(int(k) for k in choices if str(k).isdigit())
            if p.get("NextChoiceId") is not None and p["NextChoiceId"] <= max_choice:
                err(f"{path}: SQ {qid}: NextChoiceId {p['NextChoiceId']} <= max choice {max_choice}")

    # --- Cross-refs: blocks <-> questions ---
    for qid, bids in question_refs.items():
        if qid not in sq_by_qid:
            err(f"{path}: block(s) {bids} reference missing question {qid}")
        if len(bids) > 1:
            warn(f"{path}: question {qid} appears in multiple blocks {bids}")
    trash_blocks = {b["ID"] for b in block_ids.values() if b.get("Type") == "Trash"}
    for qid in sq_by_qid:
        if qid not in question_refs:
            warn(f"{path}: question {qid} not referenced by any block")

    # --- Flow ---
    if "FL" in by_type:
        flow = (by_type["FL"][0].get("Payload") or {}).get("Flow", [])
        flow_ids = set()
        for node in flow:
            fid = node.get("FlowID")
            if fid in flow_ids:
                err(f"{path}: duplicate FlowID {fid}")
            flow_ids.add(fid)
            target = node.get("ID")
            if node.get("Type") in ("Block", "Standard") and target not in block_ids:
                err(f"{path}: flow node {fid} references missing block {target}")
            if target in trash_blocks:
                err(f"{path}: flow node {fid} references Trash block {target}")

    # --- Scoring ---
    # Qualtrics scoring-category IDs are "SC_" + exactly 15 alphanumeric chars
    # (no underscores in the body). Malformed IDs fail the whole import.
    sc_id_re = re.compile(r"^SC_[A-Za-z0-9]{15}$")
    sco_cat_ids = set()
    if "SCO" in by_type:
        sco = by_type["SCO"][0].get("Payload") or {}
        for cat in sco.get("ScoringCategories", []):
            cid = cat.get("ID")
            if cid in sco_cat_ids:
                err(f"{path}: duplicate scoring category ID {cid!r}")
            if cid is not None and not sc_id_re.match(cid):
                err(f"{path}: scoring category ID {cid!r} is not Qualtrics format "
                    f"(SC_ + 15 alphanumeric chars) — import will fail")
            sco_cat_ids.add(cid)
    # GradingData is one entry per matrix cell (ChoiceID x AnswerID); each
    # entry's Grades maps category ID -> that cell's point value. See SCHEMA.md.
    for qid, p in sq_by_qid.items():
        answer_ids = {str(a) for a in (p.get("Answers") or {})}
        legacy_entries = 0
        for g in p.get("GradingData") or []:
            cid_choice = str(g.get("ChoiceID"))
            if cid_choice not in (p.get("Choices") or {}):
                err(f"{path}: SQ {qid}: GradingData for missing choice {cid_choice}")
            grades = g.get("Grades") or {}
            if "Category" in g:  # pre-2026-07 flat shape, never valid in a real export
                legacy_entries += 1
                if sco_cat_ids and g["Category"] not in sco_cat_ids:
                    err(f"{path}: SQ {qid}: GradingData category {g['Category']!r} "
                        f"not in SCO ScoringCategories")
                continue
            if "AnswerID" in g:  # real cell shape
                aid = str(g.get("AnswerID"))
                if answer_ids and aid not in answer_ids:
                    err(f"{path}: SQ {qid}: GradingData AnswerID {aid} not in Answers")
            else:
                warn(f"{path}: SQ {qid}: GradingData entry for choice {cid_choice} has no "
                     f"AnswerID (expected one entry per matrix cell)")
            for cat_id in grades:
                if sco_cat_ids and cat_id not in sco_cat_ids:
                    err(f"{path}: SQ {qid}: GradingData category {cat_id!r} "
                        f"not in SCO ScoringCategories")
        if legacy_entries:
            warn(f"{path}: SQ {qid}: {legacy_entries} GradingData entries use the pre-2026-07 "
                 f"flat Category shape; regenerate — real Qualtrics exports use per-cell entries")

    # --- QC ---
    for el in by_type.get("QC", []):
        declared = el.get("SecondaryAttribute")
        actual = len(sq_by_qid)
        if declared is not None and str(actual) != str(declared):
            warn(f"{path}: QC declares {declared} questions, file has {actual} "
                 f"(QC counts Trash questions too)")


def main():
    if len(sys.argv) < 2:
        print(__doc__.strip(), file=sys.stderr)
        sys.exit(2)
    for path in sys.argv[1:]:
        lint(path)
    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")
    if not warnings and not errors:
        print("OK: no problems found")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
