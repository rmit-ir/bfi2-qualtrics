"""Apply versioned ASE Part 1 content edits to a private native QSF export."""
import argparse
from copy import deepcopy
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sys

VERSION = "ase-part1-2026-11-24-v1.1"
BASE_AGE = ("18–24", "25–34", "35–44", "45+", "Prefer not to say")
BASE_EDUCATION = ("Below secondary", "Secondary", "Tertiary", "Postgraduate", "Other", "Prefer not to say")
AGE = ("18–24", "25–34", "35–44", "45–54", "55–64", "65+", "Prefer not to say")
EDUCATION_TEXT = (
    "What is the highest level of education you have completed?<br>"
    "Choose one. If you are currently studying, choose the highest level you have "
    "already completed. Include equivalent qualifications from any country."
)
EDUCATION = (
    "Below secondary (e.g. primary school only or no formal schooling)",
    "Secondary school (e.g. Year 10, Year 12 or equivalent)",
    "Certificate, diploma or associate degree (e.g. a trade certificate or advanced diploma)",
    "Bachelor’s degree (e.g. a Bachelor of Arts or Science, with or without honours)",
    "Postgraduate qualification (e.g. a graduate certificate, graduate diploma, master’s degree or doctorate)",
    "Other (my education does not fit the categories above)",
    "Prefer not to say",
)
BASE_ORDER = (7, None, 1, 2, 3, 8, 4, 5, 6, 9, 10, 11, 12, 13)
TARGET_ORDER = BASE_ORDER[:9] + (10, 13, 9, 12, 11)
TAGS = ("age", "gender", "education", "country", "region", "city",
        "participant_information", "ai_self_identification_signal", "search_frequency",
        "search_platforms", "search_confidence", "genai_frequency", "genai_tools")
UNSAFE_SO = ("RecaptchaV3", "IncludeSecurityFields")
_CHOICE_DEPENDENCIES = ("Answers", "AnswerOrder", "GradingData", "DisplayLogic", "ChoiceDisplayLogic",
                        "SkipLogic", "DefaultChoices", "VariableNaming", "ChoiceDataExportTags",
                        "AnswerDataExportTags", "QuestionJS", "Language")
_LINK_TO_CHANGED_QUESTION = re.compile(r"(?<![A-Za-z0-9_])QID(?:1|3)(?![A-Za-z0-9_])")


class BadBaseline(ValueError):
    """A fixed diagnostic safe to use with non-public input."""


def require(condition, reason):
    if not condition:
        raise BadBaseline(reason)


def _object(pairs):
    obj = {}
    for key, value in pairs:
        require(key not in obj, "repeated JSON key")
        obj[key] = value
    return obj


def _fail_constant(_):
    raise BadBaseline("nonfinite JSON number")


def _refs(value):
    if isinstance(value, str):
        return bool(_LINK_TO_CHANGED_QUESTION.search(value))
    if isinstance(value, dict):
        return any(_refs(key) or _refs(v) for key, v in value.items())
    if isinstance(value, list):
        return any(_refs(v) for v in value)
    return False


def _block_order(elements):
    names = ["Page Break" if index is None else f"QID{index}" for index in BASE_ORDER]
    result = []
    for element in elements:
        if element.get("Type") == "Page Break" and not element.get("QuestionID"):
            result.append("Page Break")
        elif element.get("Type") == "Question":
            result.append(element.get("QuestionID"))
        else:
            raise BadBaseline("unsupported default block element")
    require(result == names, "unexpected default block order")
    return {index: element for index, element in zip(BASE_ORDER, elements)}


def _choices(question, original_labels, labels, missing_counter_floor=None):
    require(all(not question.get(field) for field in _CHOICE_DEPENDENCIES),
            "unsupported age or education choice dependencies")
    require(not question.get("Randomization") or question["Randomization"].get("Type") in ("None", ""),
            "unexpected age or education randomization")
    old, order = question["Choices"], question["ChoiceOrder"]
    require(isinstance(old, dict) and isinstance(order, list) and order, "missing age or education choices")
    require(all(type(key) in (int, str) and re.fullmatch(r"[1-9][0-9]{0,7}", str(key))
                for key in order), "bad age or education choice order")
    require(len(order) == len(old) and set(map(str, order)) == old.keys() and
            all(type(key) is type(order[0]) for key in order), "ambiguous age or education choice order")
    require(all(isinstance(choice, dict) and set(choice) == {"Display"} and
                isinstance(choice["Display"], str) for choice in old.values()),
            "unsupported age or education choice metadata")
    require(tuple(old[str(key)]["Display"] for key in order) == original_labels,
            "unexpected baseline age or education choice labels")
    by_label = {choice["Display"]: int(key) for key, choice in old.items()}
    max_existing = max(map(int, old))
    if "NextChoiceId" in question:
        require(missing_counter_floor is None, "cannot override native NextChoiceId")
        next_id = question["NextChoiceId"]
    else:
        # A new inactive copy has no inherited responses. Start above every
        # source ID, including labels that will be removed, and allow a
        # separately reviewed higher floor for IDs seen in retained history.
        next_id = max_existing + 1 if missing_counter_floor is None else missing_counter_floor
    require(type(next_id) is int and max_existing < next_id <= 99999990,
            "invalid age or education NextChoiceId")
    recodes = question.get("RecodeValues")
    require(not recodes or (isinstance(recodes, dict) and
                           all(key in old and type(value) in (int, str) and str(value) == key
                               for key, value in recodes.items())), "unsupported age or education recodes")
    new = {}
    for label in labels:
        key = by_label.get(label)
        if key is None:
            key = next_id
            next_id += 1
        new[str(key)] = {"Display": label}
    question["Choices"] = new
    question["ChoiceOrder"] = list(map(type(order[0]), new))
    question["NextChoiceId"] = next_id
    if recodes:
        question["RecodeValues"] = {key: value for key, value in recodes.items() if key in new}


def prepare_draft(original, *, age_next_choice_id=None, education_next_choice_id=None):
    """Return a copy; never mutate the caller's private baseline."""
    try:
        draft = deepcopy(original)
        survey_id = draft["SurveyEntry"]["SurveyID"]
        require(draft["SurveyEntry"]["SurveyStatus"] in ("Inactive", "New"),
                "baseline is not an inactive/new survey export")
        require(type(survey_id) is str and re.fullmatch(r"SV_[A-Za-z0-9]+", survey_id),
                "missing native survey ID")
        elements = draft["SurveyElements"]
        require(isinstance(elements, list) and len(elements) <= 1000 and
                all(isinstance(e, dict) and e.get("SurveyID") == survey_id for e in elements),
                "survey element identity mismatch")
        groups = {kind: [e for e in elements if e["Element"] == kind]
                  for kind in ("SQ", "BL", "SO", "FL")}
        require(all(len(groups[kind]) == 1 for kind in ("BL", "SO", "FL")),
                "ambiguous survey block, flow or options")
        questions = {e["PrimaryAttribute"]: e for e in groups["SQ"]}
        require(len(groups["SQ"]) == len(questions) == len(TAGS), "unexpected Part 1 question inventory")
        for i, tag in enumerate(TAGS, 1):
            qid = f"QID{i}"
            payload = questions[qid]["Payload"]
            require((payload["QuestionID"], payload["DataExportTag"]) == (qid, tag),
                    "Part 1 question identity mismatch")
            expected = ("DB", "TB", None) if i == 7 else ("TE", "SL", None) if i in (4, 5, 6) else (
                "MC", "MAVR" if i in (10, 13) else "SAVR", "TX")
            require((payload["QuestionType"], payload["Selector"]) == expected[:2] and
                    (expected[2] is None or payload.get("SubSelector") == expected[2]),
                    "Part 1 question format mismatch")
            if i != 7:
                require(payload["Validation"]["Settings"]["ForceResponse"] == "OFF",
                        "unexpected required answer")
            if i in (10, 13):
                pairs = [(v["Display"], v.get("ExclusiveAnswer") is True, v.get("TextEntry"))
                         for v in payload["Choices"].values()]
                require(sorted(label for label, exclusive, _ in pairs if exclusive) ==
                        ["None", "Prefer not to say"] and not any(
                            entry not in (None, False, "false", "OFF", "off", "") for _, _, entry in pairs),
                        "unexpected multiple-answer choice behavior")

        blocks = groups["BL"][0]["Payload"]
        if isinstance(blocks, dict):
            blocks = list(blocks.values())
        require(isinstance(blocks, list), "unsupported block shape")
        active = [block for block in blocks if block["Type"] != "Trash"]
        require(len(active) == 1 and active[0]["Type"] == "Default", "ambiguous default block")
        block = active[0]
        require(not block.get("Options", {}).get("RandomizeQuestions") and
                not block.get("Options", {}).get("Randomization"), "unsupported block randomization")
        by_item = _block_order(block["BlockElements"])
        flow = groups["FL"][0]["Payload"]
        require(flow["Type"] == "Root" and sum(
            f.get("ID") == block["ID"] and f.get("Type") in ("Block", "Standard") for f in flow["Flow"]
        ) == 1, "default block missing or repeated in root flow")
        require(not any(_refs(e["Payload"]) for e in elements
                        if e["Element"] not in ("BL", "SQ")) and
                not any(_refs(e["Payload"]) for qid, e in questions.items() if qid not in ("QID1", "QID3")),
                "another survey element references age or education")
        _choices(questions["QID1"]["Payload"], BASE_AGE, AGE, age_next_choice_id)
        education = questions["QID3"]
        _choices(education["Payload"], BASE_EDUCATION, EDUCATION, education_next_choice_id)
        education["Payload"]["QuestionText"] = EDUCATION_TEXT
        education["Payload"]["QuestionDescription"] = EDUCATION_TEXT.split("<br>", 1)[0]
        education["SecondaryAttribute"] = education["Payload"]["QuestionDescription"]
        block["BlockElements"] = [by_item[i] for i in TARGET_ORDER]
        options = groups["SO"][0]["Payload"]
        for field in UNSAFE_SO:
            options.pop(field, None)
        return draft
    except (KeyError, TypeError, AttributeError, IndexError):
        raise BadBaseline("unsupported native QSF structure") from None


def _outside_git(path):
    return not any((parent / ".git").exists() for parent in path.parents)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True, help="private native QSF outside Git")
    parser.add_argument("--baseline-sha256", required=True, help="expected SHA256 of the reviewed export")
    parser.add_argument("--age-next-choice-id", type=int, help="reviewed age floor if native counter is absent")
    parser.add_argument("--education-next-choice-id", type=int,
                        help="reviewed education floor if native counter is absent")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="validate transformation without writing")
    mode.add_argument("--output", type=Path, help="new private QSF outside Git")
    args = parser.parse_args(argv)

    try:
        baseline = args.baseline.resolve()
        require(baseline.suffix == ".qsf" and _outside_git(baseline) and _outside_git(args.baseline.absolute()),
                "baseline must be a QSF outside Git")
        with baseline.open("rb") as source:
            raw = source.read(8 * 1024 * 1024 + 1)
        require(len(raw) <= 8 * 1024 * 1024, "baseline too large")
        require(re.fullmatch(r"[a-f0-9]{64}", args.baseline_sha256) and
                hmac.compare_digest(hashlib.sha256(raw).hexdigest(), args.baseline_sha256),
                "baseline digest mismatch")
        draft = prepare_draft(
            json.loads(raw, object_pairs_hook=_object, parse_constant=_fail_constant),
            age_next_choice_id=args.age_next_choice_id,
            education_next_choice_id=args.education_next_choice_id,
        )
        if args.output:
            path = args.output.resolve()
            require(path.suffix == ".qsf" and _outside_git(path) and _outside_git(args.output.absolute()),
                    "output must be a QSF outside Git")
            content = json.dumps(draft, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
            with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
                output.write(content)
    except BadBaseline as exc:
        print(f"{VERSION}: refused: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError, RecursionError):
        print(f"{VERSION}: could not read, validate or write private QSF", file=sys.stderr)
        return 2
    print(f"{VERSION}: {'validated; no file written' if args.check else 'private draft written; not imported or published'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
