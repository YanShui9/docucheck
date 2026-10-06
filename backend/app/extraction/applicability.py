"""Narrow, deterministic proof that a whole paragraph concerns another model."""
import re

from ..evidence.models import ScopedEvidence

_FOREIGN = re.compile(
    r"(?:宣传参考\s*[:：]\s*)?另一型号\s+([A-Za-z0-9][A-Za-z0-9._-]{0,79})\s+"
    r"(?:已装内存|installed\s+RAM)\s+\d+(?:\.\d+)?\s*(?:MiB|GiB|GB)\s*"
    r"[,，]\s*不属于本次报价[。.]?", re.IGNORECASE,
)


def excluded_memory_source(block: ScopedEvidence) -> bool:
    """Only an entirely explicit foreign-model note may be excluded.

    Fullmatch prevents adjacent current-model facts or document commands from
    being hidden. A missing binding, requirement, or equal model is not proof.
    The original evidence is still scanned and remains readable.
    """
    if block.role == "requirement" or not block.model or block.cell is not None:
        return False
    match = _FOREIGN.fullmatch(block.text.strip())
    return bool(match and match[1].casefold() != block.model.strip().casefold())
