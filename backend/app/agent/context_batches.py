"""Bounded internal request parts; immutable parent plans remain authoritative."""

import re
from collections.abc import Callable

from .policy import RunStopped


def _source_groups(evidence: list[dict]) -> list[list[dict]]:
    groups = {}
    for block in evidence:
        cell = re.fullmatch(r"[A-Za-z]+([1-9][0-9]*)", block.get("cell") or "")
        key = ((block.get("document_id"), block["sheet"], int(cell[1]))
               if cell and block.get("sheet") else (block["evidence_id"],))
        groups.setdefault(key, []).append(block)
    return list(groups.values())


def repack_review_request(data: dict, fits: Callable[[dict], bool]) -> list[dict]:
    """At most three genuine splits, preserving every criterion/source pair.

    Cross-block presence/material and manual clauses keep their original source
    context. A request we cannot safely split fails before any child is sent.
    """
    parts = [data]
    splits = 0
    index = 0
    while index < len(parts):
        part = parts[index]
        if fits(part):
            index += 1
            continue
        if splits >= 3:
            raise RunStopped("context_budget")
        criteria = part["criteria"]
        if len(criteria) > 1:
            middle = len(criteria) // 2
            children = [{**part, "criteria": group} for group in (criteria[:middle], criteria[middle:])]
        else:
            if any(c["rule"]["type"] in {"presence", "manual"} for c in criteria):
                raise RunStopped("context_budget")
            groups = _source_groups(part["evidence"])
            if len(groups) < 2:
                raise RunStopped("context_budget")
            middle = len(groups) // 2
            children = [{**part, "evidence": [b for group in section for b in group]}
                        for section in (groups[:middle], groups[middle:])]
        parts[index:index + 1] = children
        splits += 1
    return parts
