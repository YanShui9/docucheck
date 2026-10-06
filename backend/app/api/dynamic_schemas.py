"""Source-supported dynamic fact review contracts."""

from pydantic import Field

from ..criteria.facts import FactCandidate
from ..criteria.models import Contract


class DynamicCorrectionIn(Contract):
    fact_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate: FactCandidate
    reason: str = Field(min_length=1, max_length=1000, pattern=r"\S")
    expected_revision: int = Field(ge=1)
    expected_payload_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
