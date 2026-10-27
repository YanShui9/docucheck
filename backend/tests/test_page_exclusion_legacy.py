"""v15 identities captured from committed 4fed2c7, before exclusion changes."""

import pytest

from backend.app.agent.dynamic import review_plan
from backend.app.criteria.facts import revalidate_fact
from backend.app.evidence.repository import digest
from backend.tests.test_bound_enum_headers import checks, verify
from backend.tests.test_material_sources import material_sample
from backend.tests.test_money_consistency import prices
from backend.tests.test_money_sources import sample

GOLDEN = {
    "equivalent": (
        "b90be9cace006fa460dc7bed057f134a1519b1a060b92578f2cef5a810afa490",
        "e2d9fddaef401e70d3135158f2332d10040a6a535c8411b995bbc8b508403536",
        "c6d3d8c49a40732dfa431fea79aa4d7d8e3511fdfed04587505f8b2ea562807c",
        "44a927705cfa877b293111c67fe29483653cf3370fa9e80bb7394eaeba141416"),
    "different": (
        "453d1d6dbb656ad531f8f6f31871ef36e9be08ba281363e2026c6cd21c495d58",
        "738ee3f230665d3b145eaa67c4aad6faba2d1aaeb76ad85b1a6582861e7fcc12",
        "db4414a7d2b2bc8e2ca00b85dbf9575ddba18917bd22bceb0ac402ef77b4858a",
        "2b9bc9134dca5a67aab9401af8d3b80ed373eb55ed96f36a4bac275d9569cd55"),
    "tax_conflict": (
        "0188eda675321fecf15ba46c57236fb1d93bfaf51e6d4628dc6f9a8b61210155",
        "b2b4228a43b911fc140a1591b74023f8fb24af21fb1f9ea102361b4ab3d4e2d7",
        "bb9207a4d0ae53868a529daa5a3895682a073aa7bf5042068bb8bfdb200336bf",
        "1cb55b2a58606320385ce3c6806127782cc29cb68de07b9f7c5a2d7601214795"),
    "material": (
        "fec85455c07a67f1f16b3264d947104df9d8dcae5c4335c9c149427bb812d477",
        "72c23f2c8464bbdffa2d5c12515a98066632684b53289eb3e4a841ce7a11aafa",
        "b97a43ca0be158f880f3c75c93ae214e97c510f8c9643b73b493b425c730a241",
        "0485a85d53badf3d467915037d7251142086a79e170f2ea76b5adadc94569c42"),
}


@pytest.mark.parametrize("name", GOLDEN)
def test_v15_full_fact_check_and_request_identity_stays_fixed(name):
    built, body = {"equivalent": prices, "different": lambda: prices(other="含税单价 5900 元/台"),
                   "tax_conflict": lambda: sample(tax="含税"), "material": material_sample}[name]()
    version = "dynamic-rules-v15"
    fact = verify(built, body, version)
    check = checks(built, [fact], version)[0]
    entry = review_plan(built[1], built[0], rule_version=version, item_bindings=built[2])[0]["A"][0][2]
    assert (digest(fact), digest(check), entry["batch_id"], entry["input_digest"]) == GOLDEN[name]
    assert revalidate_fact(fact, built[0], built[1], "A", rule_version=version, item_bindings=built[2]) == fact
    current = checks(built, [verify(built, body, "dynamic-rules-v16")], "dynamic-rules-v16")[0]
    assert current["business_status"] == check["business_status"]
    assert current["reason_code"] == check["reason_code"]
