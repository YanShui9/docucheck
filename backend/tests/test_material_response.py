"""Response filtering must preserve source groups without changing legacy output."""

from backend.app.api.schemas import CheckOut
from backend.tests.test_bound_enum_headers import checks, source, verify
from backend.tests.test_material_sources import independent_documents


def test_material_groups_survive_public_response_filtering():
    sample, proposal = independent_documents()
    fact = verify(sample, proposal)
    check = checks(sample, [fact])[0]
    encoded = CheckOut.model_validate(check).model_dump(mode="json")
    assert encoded["material_source_groups"] == check["material_source_groups"]


def test_legacy_check_response_has_no_empty_material_group_field():
    sample = source()
    check = checks(sample, [verify(sample)], "dynamic-rules-v10")[0]
    assert "material_source_groups" not in CheckOut.model_validate(check).model_dump(mode="json")
