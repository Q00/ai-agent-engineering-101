"""Skip an offer only when a quote explicitly states terms the order's accept policy refuses.

This is an extra filter and never a reason to trust a seller: a passing value proves nothing, and the kit still
checks the market certificate before paying. Missing, vague or lower-bound values do not block anything.
"""

import math
import re
import time

GRADE_KEYS = ("grade", "grade_now")
HOUR_KEYS = ("hours_left", "shelf_hours_left")
GRADE_VALUE = re.compile(r"[A-F]")
NUMBER = r"\d[\d,]*(?:\.\d+)?"
QUALIFIER = r"(?:(at least|over|more than|min(?:imum)?|above|about|around|approx(?:imately)?|roughly|~)\s*)?"
HOUR_UNIT = r"(h|hrs?|hours?|days?)(?![^\W\d_])"
LEFT = r"(?:left|remaining)"
TEXT_GRADES = (re.compile(r"(?<![\w])(?i:grade)(?:\s+is)?\s*[:=]?\s*([A-F])(?![\w+-])"),
               re.compile(r"(?<![\w])등급\s*[:=]?\s*([A-F])(?![\w+-])"),
               re.compile(r"(?<![\w])([A-F])\s*등급"))
NEGATION = {"not", "no", "than", "except", "other", "above", "below", "without", "if"}
TEXT_HOURS = (re.compile(QUALIFIER + "(" + NUMBER + r")\s*" + HOUR_UNIT + r"\s*(?:of\s+)?shelf[ _-]?(?:life[ _-]?)?" + LEFT, re.I),
              re.compile(r"shelf[ _-]?(?:life[ _-]?|hours[ _-]?)?" + LEFT + r"\s*(?:[:=]|is|of)?\s*" + QUALIFIER
                         + "(" + NUMBER + r")\s*" + HOUR_UNIT, re.I),
              re.compile(r"remaining\s+shelf[ -]?life\s*(?:[:=]|is)?\s*" + QUALIFIER
                         + "(" + NUMBER + r")\s*" + HOUR_UNIT, re.I),
              re.compile(r"(?:유통|소비|사용)기한[^\d\n]{0,8}()(" + NUMBER + r")\s*(시간|일)"))
HOURS_PER_UNIT = {"d": 24, "일": 24}
VAGUE_SHELF = re.compile(r"shelf[ -]?life|유통기한|소비기한|close to its date", re.I)


def _number(value):
    if type(value) in (int, float) and math.isfinite(value):
        return float(value)
    if isinstance(value, str) and re.fullmatch(NUMBER, value.strip()):
        return float(value.strip().replace(",", ""))
    return None


def _scopes(task):
    """Every place a shop has been seen to put quote terms, including nested offer terms and certificates."""
    status = task.get("status")
    message = status.get("message") if isinstance(status, dict) else None
    tops = [task.get("metadata")]
    if isinstance(message, dict):
        tops.append(message.get("metadata"))
        parts = message.get("parts")
        for part in parts if isinstance(parts, list) else ():
            if isinstance(part, dict):
                tops += [part.get("data"), part.get("metadata")]
    for top in tops:
        if not isinstance(top, dict):
            continue
        yield top, False
        terms = top.get("offer_terms")
        if isinstance(terms, dict):
            yield terms, True
        cert = top.get("cert")
        if isinstance(cert, dict) and isinstance(cert.get("cert"), dict):
            yield cert["cert"], True


def structured_terms(task, now):
    grades, hours = set(), []
    for scope, is_terms in _scopes(task):
        for key in GRADE_KEYS:
            value = scope.get(key)
            if isinstance(value, str) and GRADE_VALUE.fullmatch(value.strip().upper()):
                grades.add(value.strip().upper())
        for key in HOUR_KEYS:
            value = _number(scope.get(key))
            if value is not None and value >= 0:
                hours.append(value)
        # A bare expires_at may be a quote's own validity; only certificates and offer terms name the unit's date.
        expires = _number(scope.get("expires_at")) if is_terms else None
        if expires is not None:
            hours.append((expires - now) / 3600)
    return grades, hours


def text_grades(text):
    """Return (distinct stated grades, ambiguous). Several grades or a negated one is not a statement of this unit."""
    found, ambiguous = set(), False
    for pattern in TEXT_GRADES:
        for match in pattern.finditer(text):
            before = re.search(r"([^\W\d_]+)\s+$", text[:match.start()])
            if before and before[1].casefold() in NEGATION:
                ambiguous = True
            else:
                found.add(match[1])
    if len(found) > 1:
        ambiguous = True
    return (set() if ambiguous else found), ambiguous


def text_hours(text):
    """Return (value or None, note). Lower bounds cannot show a shortfall; 'about' gets a margin before use."""
    values, lower_bound = set(), False
    for pattern in TEXT_HOURS:
        for match in pattern.finditer(text):
            qualifier = (match[1] or "").casefold()
            hours = float(match[2].replace(",", "")) * HOURS_PER_UNIT.get(match[3][0].casefold(), 1)
            if qualifier in ("at least", "over", "more than", "min", "minimum", "above"):
                lower_bound = True
            elif qualifier:
                values.add(hours * 1.1)
            else:
                values.add(hours)
    if len(values) > 1:
        return None, "ambiguous"
    if values:
        return values.pop(), "stated"
    if lower_bound:
        return None, "lower_bound"
    return None, "vague" if VAGUE_SHELF.search(text) else "absent"


def _basis(structured, text):
    return "structured+text" if structured and text else "structured" if structured else "text" if text else None


def check_quote(task, text, accept, now=None):
    """Compare what a quote states with accept conditions. reason_code is set only for an explicit violation."""
    now = time.time() if now is None else now
    grades, hours = structured_terms(task, now)
    said_grades, grade_ambiguous = text_grades(text)
    said_hours, hours_note = text_hours(text)
    conflicts = []
    all_grades = grades | said_grades
    worst = max(all_grades) if all_grades else None
    if len(all_grades) > 1:
        conflicts.append("grade")
    all_hours = hours + ([said_hours] if said_hours is not None else [])
    lowest = min(all_hours) if all_hours else None
    if all_hours and max(all_hours) - lowest > max(1, 0.02 * max(all_hours)):
        conflicts.append("hours_left")
    grade_basis = _basis(worst in grades, worst in said_grades) if worst else None
    hours_basis = _basis(lowest in hours, lowest == said_hours) if lowest is not None else None
    result = {"reason_code": None, "basis": None, "grade": worst, "grade_basis": grade_basis,
              "hours_left": round(lowest, 2) if lowest is not None else None, "hours_basis": hours_basis,
              "text_hours_note": hours_note, "text_grade_ambiguous": grade_ambiguous, "conflicts": conflicts,
              "accept_grades": accept.get("grades"), "min_hours_left": accept.get("min_hours_left")}
    required = accept.get("min_hours_left")
    if type(required) in (int, float) and lowest is not None and lowest < required:
        result.update(reason_code="quote_shelf_life_short", basis=hours_basis)
    else:
        allowed = accept.get("grades")
        if isinstance(allowed, list) and allowed and worst is not None and worst not in allowed:
            result.update(reason_code="quote_grade_unacceptable", basis=grade_basis)
    return result
