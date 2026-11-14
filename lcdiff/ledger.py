"""Accepted-drift ledger: blessed findings, suppressed by identity.

A ledger is a small JSON file:

    {"version": 1,
     "accepted": [{"endpoint": "GET /users/{id}", "code": "TYPE_MISMATCH",
                   "field": "data.age",
                   "reason": "why this drift is tolerated",
                   "accepted_at": "2026-11-14"}]}

A finding is accepted iff its (endpoint, code, field) identity matches
an entry exactly — never line numbers (findings have none) and never
evidence (evidence carries counts that legitimately change between
runs, so keying on it would make acceptances silently lapse). Accepted
findings are reported in their own section, with their reasons, and
never affect the exit code; removing an entry makes the finding
reappear on the next run.

Validation is strict, like baselines (D1): a malformed ledger is a
clean error, never silently ignored — a typo'd ledger that quietly
stopped suppressing, or a duplicate that hides a second acceptance,
would be worse than no ledger. An acceptance must carry a non-empty
reason: the ledger is the team's record of *why* each drift is OK.
"""
from . import har as har_mod

LEDGER_VERSION = 1
_ENTRY_KEYS = {"endpoint", "code", "field", "reason", "accepted_at"}


def finding_key(f):
    return (f.get("endpoint", ""), f.get("code", ""), f.get("field", ""))


def validate_ledger(data, source="<ledger>"):
    """Strict structural validation. Raises IncompleteInputError."""
    E = har_mod.IncompleteInputError
    if not isinstance(data, dict):
        raise E(f"{source}: ledger is not a JSON object")
    if data.get("version") != LEDGER_VERSION:
        raise E(f"{source}: ledger version {data.get('version')!r} is "
                f"not supported (expected {LEDGER_VERSION})")
    entries = data.get("accepted")
    if not isinstance(entries, list):
        raise E(f"{source}: ledger 'accepted' must be a list")
    seen = set()
    for i, e in enumerate(entries):
        if not isinstance(e, dict):
            raise E(f"{source}: accepted entry {i} is not an object")
        unknown = set(e) - _ENTRY_KEYS
        if unknown:
            raise E(f"{source}: accepted entry {i} has unknown keys "
                    f"{sorted(unknown)}")
        for k in ("endpoint", "code", "field", "reason"):
            if not isinstance(e.get(k), str):
                raise E(f"{source}: accepted entry {i}: {k!r} must be "
                        "a string")
        if not e["reason"].strip():
            raise E(f"{source}: accepted entry {i} has an empty "
                    "reason — an acceptance must say why the drift "
                    "is tolerated")
        if "accepted_at" in e and not isinstance(e["accepted_at"], str):
            raise E(f"{source}: accepted entry {i}: 'accepted_at' "
                    "must be a string")
        key = (e["endpoint"], e["code"], e["field"])
        if key in seen:
            raise E(f"{source}: duplicate acceptance for {key}")
        seen.add(key)
    return data


def load_ledger(path):
    E = har_mod.IncompleteInputError
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        raise E(f"ledger file not found: {path}")
    except (OSError, UnicodeDecodeError) as e:
        raise E(f"ledger unreadable: {path}: {e}")
    try:
        data = har_mod.loads_no_duplicates(text, source=path)
    except Exception as e:
        raise E(f"ledger is not strict JSON: {path}: {e}")
    return validate_ledger(data, source=path)


def apply_ledger(findings, ledger):
    """Split findings into (active, accepted).

    Accepted findings are copies carrying 'accepted_reason' (and
    'accepted_at' when the entry has one); the inputs are not mutated.
    """
    by_key = {(e["endpoint"], e["code"], e["field"]): e
              for e in (ledger or {}).get("accepted", [])}
    active, accepted = [], []
    for f in findings:
        entry = by_key.get(finding_key(f))
        if entry is None:
            active.append(f)
            continue
        g = dict(f)
        g["accepted_reason"] = entry["reason"]
        if entry.get("accepted_at"):
            g["accepted_at"] = entry["accepted_at"]
        accepted.append(g)
    return active, accepted


def ledger_from_findings(findings, reason, existing=None,
                         accepted_at=None):
    """Build/extend a ledger dict from a report's findings.

    Findings whose identity is already accepted keep their original
    entry (reason and date); only new identities are appended. Entries
    are sorted by identity so ledger diffs stay reviewable.
    """
    entries = [dict(e) for e in (existing or {}).get("accepted", [])]
    seen = {(e["endpoint"], e["code"], e["field"]) for e in entries}
    for f in findings:
        key = finding_key(f)
        if key in seen:
            continue
        seen.add(key)
        entry = {"endpoint": key[0], "code": key[1], "field": key[2],
                 "reason": reason}
        if accepted_at:
            entry["accepted_at"] = accepted_at
        entries.append(entry)
    entries.sort(key=lambda e: (e["endpoint"], e["code"], e["field"]))
    return {"version": LEDGER_VERSION, "accepted": entries}
