"""Material citations travel as independent, source-checked groups offline."""

import json
from copy import deepcopy
from dataclasses import replace

import pytest
from langchain_core.messages import HumanMessage

from backend.app.agent import dynamic
from backend.app.agent.dynamic import DynamicAgent, DynamicFactCandidates, _facts_schema
from backend.app.agent.policy import Budget
from backend.app.criteria.facts import BINDING_RULE_VERSION, validate_fact
from backend.app.criteria.item_bindings import ItemBindingEntry, validate_bindings
from backend.app.evidence.models import Document
from backend.app.evidence.repository import EvidenceRepository, digest
from backend.app.parsing import EvidenceBlock, ParseResult
from backend.app.providers.base import ProviderError
from backend.tests.test_bound_enum_headers import source
from backend.tests.test_dynamic_agent import call


def material_sample(*, body="样品 检测报告编号JC-01，检测结果合格"):
    return source("检测报告", {"type": "presence", "description": "检测报告"},
                  [("B6", "检测报告 已提供检测报告"), ("B7", body)])


def material_candidate(repo):
    claim, body = repo.blocks[1:]
    return {"criterion_id": "metric", "item_id": "sample", "supplier": "A",
            "value": True, "unit": "", "evidence_refs": [claim.evidence_id], "quote": claim.text,
            "material_sources": [{"evidence_refs": [body.evidence_id], "quote": body.text}]}


def separate_document_sample():
    definition, original, frozen = material_sample()
    quote = original.documents[0]
    quote = replace(quote, parsed=replace(quote.parsed, blocks=quote.parsed.blocks[:2]))
    material = Document("material", "b" * 64, "spec", "A", None,
                        ParseResult("material", "parser", "complete", (
                            EvidenceBlock("body-1", "material", "样品 检测报告编号JC-01", page_idx=0),
                            EvidenceBlock("body-2", "material", "检测报告：检测结果合格", page_idx=1),
                        ), total_pages=2, processed_pages=(0, 1)))
    repo = EvidenceRepository("case", 1, [quote, material])
    quote_binding = {key: value for key, value in frozen["payload"]["entries"][0].items()
                     if key in ItemBindingEntry.model_fields}
    quote_binding["anchor"] = {"evidence_refs": [repo.blocks[0].evidence_id], "quote": repo.blocks[0].text}
    entries = [{**quote_binding, "selector": {
        "kind": "xlsx", "sheet": "报价", "start_row": 6, "end_row": 6,
        "start_column": 1, "end_column": 2}}, {
            "document_id": "material", "source_sha256": "b" * 64, "parser_version": "parser",
            "item_id": "sample", "selector": {"kind": "whole"}, "single_item_confirmed": True,
            "anchor": {"evidence_refs": [repo.blocks[2].evidence_id], "quote": repo.blocks[2].text},
            "identifier_class": "category",
        }]
    payload = {**frozen["payload"], "snapshot_id": repo.snapshot_id,
               "entries": validate_bindings(entries, definition, repo).payload()}
    return definition, repo, {"version_id": "binding-separated", "digest": digest(payload), "payload": payload}


class MaterialProvider:
    context_window = 32768

    def __init__(self, raw, *, decision="review_checklist", repair=None, failure=None):
        self.raw, self.repair, self.failure = raw, repair, failure
        self.decision, self.requests, self.schemas = decision, [], []

    def decide(self, messages, tools, **options):
        return call(self.decision, {})

    def extract(self, messages, schema, **options):
        self.requests.append(deepcopy(messages))
        self.schemas.append(deepcopy(schema))
        if self.failure:
            raise self.failure
        raw = self.repair if self.repair is not None and len(self.requests) > 1 else self.raw
        return call(schema["function"]["name"], {"facts": deepcopy(raw)})


def run(sample, provider, **kwargs):
    definition, repo, frozen = sample
    return DynamicAgent(repo, provider, definition, item_bindings=frozen, **kwargs).ask("完整审查材料")


def feedback(messages):
    return next(json.loads(message.content) for message in messages[1:-1]
                if isinstance(message, HumanMessage))


def test_material_extraction_preserves_independent_quote_and_body_groups():
    sample = material_sample()
    raw = material_candidate(sample[1])
    provider = MaterialProvider([raw])
    result = run(sample, provider)
    assert result["status"] == "completed"
    assert len(provider.requests) == 1
    fact = result["payload"]["facts"][0]
    assert fact["quote"] == raw["quote"]
    assert fact["material_sources"] == raw["material_sources"]
    assert raw["material_sources"][0]["evidence_refs"][0] in result["answer"]


def test_separate_document_and_page_groups_pass_without_combining_their_quotes():
    sample = separate_document_sample()
    repo = sample[1]
    raw = material_candidate(material_sample()[1])
    raw.update(evidence_refs=[repo.blocks[1].evidence_id], quote=repo.blocks[1].text,
               material_sources=[{"evidence_refs": [block.evidence_id], "quote": block.text}
                                 for block in repo.blocks[2:]])
    provider = MaterialProvider([raw])
    result = run(sample, provider)
    assert result["status"] == "completed" and len(provider.requests) == 1
    fact = result["payload"]["facts"][0]
    assert fact["material_sources"] == raw["material_sources"]
    assert len(fact["material_binding_entry_digests"]) == 2
    assert "第 1 页" in result["answer"] and "第 2 页" in result["answer"]


def test_material_schema_is_versioned_and_both_supplier_variants_are_bound():
    old = _facts_schema("A")
    assert "MaterialFactCandidate" not in old["function"]["parameters"]["$defs"]
    assert "material_sources" not in DynamicFactCandidates.model_json_schema()["$defs"]["FactCandidate"]["properties"]
    schema = _facts_schema("A", material_sources=True)
    definitions = schema["function"]["parameters"]["$defs"]
    for name in ("FactCandidate", "MaterialFactCandidate"):
        assert definitions[name]["properties"]["supplier"]["enum"] == ["A"]


def test_material_cache_roundtrip_and_report_reuse_keep_all_sources():
    sample = material_sample()
    raw = material_candidate(sample[1])
    cache = {}
    first = run(sample, MaterialProvider([raw]), cache_save=lambda key, digest, data: cache.update({(key, digest): deepcopy(data)}))
    assert first["status"] == "completed" and cache
    assert next(iter(cache.values()))["facts"][0]["material_sources"] == raw["material_sources"]
    provider = MaterialProvider([], failure=AssertionError("must use cache"))
    reused = run(sample, provider, cache_load=lambda key, digest: cache.get((key, digest)))
    assert reused["status"] == "completed" and not provider.requests
    assert reused["payload"]["facts"] == first["payload"]["facts"]
    provider = MaterialProvider([], decision="get_checklist")
    readback = run(sample, provider, previous_payload=first["payload"])
    assert readback["payload"]["facts"] == first["payload"]["facts"]
    assert not provider.requests

    carried = deepcopy(first["payload"])
    carried["needs_review"] = True
    current = run(sample, MaterialProvider([]), previous_payload=carried)
    assert current["payload"]["needs_review"]
    assert current["payload"]["facts"] == first["payload"]["facts"]


def test_semantic_repair_cannot_remove_independent_material_source_to_pass():
    sample = material_sample()
    raw = material_candidate(sample[1]) | {"unit": "错误单位"}
    repaired = {key: value for key, value in raw.items() if key != "material_sources"} | {"unit": ""}
    provider = MaterialProvider([raw], repair=[repaired])
    result = run(sample, provider)
    assert result["status"] == "partial"
    assert result["payload"]["batch_coverage"][0]["repair_missing_sources"] == 1
    assert len(provider.requests) == 2
    diagnostic = feedback(provider.requests[1])["candidates"][0]
    assert "quote" not in diagnostic
    assert diagnostic["material_sources"] == [{"evidence_refs": raw["material_sources"][0]["evidence_refs"]}]
    content = next(message.content for message in provider.requests[1][1:-1] if isinstance(message, HumanMessage))
    assert len(content.encode("utf-8")) <= 2048


@pytest.mark.parametrize("reassignment", ["promote", "reparent"])
def test_semantic_repair_preserves_material_relationship_to_original_main_source(reassignment):
    sample = source("检测报告", {"type": "presence", "description": "检测报告"}, [
        ("B6", "检测报告 已提供检测报告"), ("B7", "检测报告 已附检测报告"),
        ("B8", "样品 检测报告编号JC-01，检测结果合格"),
    ])
    repo = sample[1]
    claim, other_claim, body = repo.blocks[1:]
    raw = {"criterion_id": "metric", "item_id": "sample", "supplier": "A", "value": True,
           "unit": "错误单位", "evidence_refs": [claim.evidence_id], "quote": claim.text,
           "material_sources": [{"evidence_refs": [body.evidence_id], "quote": body.text}]}
    main = {key: value for key, value in raw.items() if key != "material_sources"} | {"unit": ""}
    if reassignment == "promote":
        reassigned = main | raw["material_sources"][0]
    else:
        reassigned = main | {"evidence_refs": [other_claim.evidence_id], "quote": other_claim.text,
                             "material_sources": raw["material_sources"]}
    cached = []
    provider = MaterialProvider([raw], repair=[main, reassigned])
    result = run(sample, provider, cache_save=lambda *args: cached.append(args))
    assert result["status"] == "partial"
    assert result["payload"]["batch_coverage"][0]["repair_missing_sources"] == 1
    assert result["payload"]["batch_coverage"][0]["rejection_codes"] == {"candidate_invalid": 1}
    assert len(result["payload"]["facts"]) == 2
    assert len(provider.requests) == 2 and not cached


@pytest.mark.parametrize("failure", [ProviderError("timeout", outcome_unknown=True), None])
def test_material_calls_still_share_budget_and_unknown_requests_are_not_retried(failure):
    sample = material_sample()
    provider = MaterialProvider([material_candidate(sample[1])], failure=failure)
    result = run(sample, provider, budget=Budget(max_calls=1 if failure is None else 16))
    assert result["status"] == "partial"
    if failure:
        assert result["error"] == "timeout" and len(provider.requests) == 1
    else:
        assert result["error"] == "model_budget" and not provider.requests


@pytest.mark.parametrize("invalid_group", ["outside_batch", "unbound", "unknown"])
def test_every_material_group_must_be_in_current_batch_before_candidate_read(monkeypatch, invalid_group):
    sample = material_sample()
    definition, repo, frozen = sample
    raw = material_candidate(repo)
    if invalid_group == "outside_batch":
        monkeypatch.setattr(dynamic, "_batches", lambda blocks: (
            [[{"evidence_id": block.evidence_id, "text": block.text, "role": block.role,
               "supplier": block.supplier, "document_id": block.document_id}]
             for block in blocks], []))
    else:
        raw["material_sources"][0]["evidence_refs"] = [
            repo.blocks[0].evidence_id if invalid_group == "unbound" else "f" * 64]
    candidate_refs = [*raw["evidence_refs"], *raw["material_sources"][0]["evidence_refs"]]
    provider = MaterialProvider([raw])
    instance = DynamicAgent(repo, provider, definition, item_bindings=frozen)
    original_read = instance._read
    reads = []

    def read(refs, budget):
        reads.append(list(refs))
        return original_read(refs, budget)

    instance._read = read
    result = instance.ask("完整审查")
    assert result["status"] == "partial" and result["payload"]["facts"] == []
    assert all(batch["rejection_codes"] == {"candidate_scope": 1}
               for batch in result["payload"]["batch_coverage"])
    assert candidate_refs not in reads
    assert not any(event.get("semantic_repair") for event in result["events"])


def test_material_source_read_keeps_post_read_snapshot_guard():
    sample = material_sample()
    definition, repo, frozen = sample
    raw = material_candidate(repo)
    original_read = repo.read
    refs = [*raw["evidence_refs"], *raw["material_sources"][0]["evidence_refs"]]

    def read(requested):
        blocks = original_read(requested)
        if list(requested) == refs:
            repo.snapshot_id = "changed-during-material-read"
        return blocks

    instance = DynamicAgent(repo, MaterialProvider([raw]), definition, item_bindings=frozen)
    repo.read = read
    result = instance.ask("完整审查")
    assert result["status"] == "partial" and result["error"] == "source_access_changed"
    assert result["payload"]["facts"] == []


def test_previous_report_cannot_carry_cross_batch_material_variant_beside_cached_base(monkeypatch):
    sample = material_sample()
    definition, repo, frozen = sample
    monkeypatch.setattr(dynamic, "_batches", lambda blocks: (
        [[{"evidence_id": block.evidence_id, "text": block.text, "role": block.role,
           "supplier": block.supplier, "document_id": block.document_id}] for block in blocks], []))

    class SingleBatchProvider(MaterialProvider):
        def extract(self, messages, schema, **options):
            block = json.loads(messages[-1].content)["evidence"][0]
            raw = {"criterion_id": "metric", "item_id": "sample", "supplier": "A", "value": True,
                   "unit": "", "evidence_refs": [block["evidence_id"]], "quote": block["text"]}
            return call(schema["function"]["name"], {"facts": [raw]})

    cache = {}
    previous = run(sample, SingleBatchProvider([]), cache_save=lambda key, digest, data: cache.update(
        {(key, digest): deepcopy(data)}))
    assert previous["status"] == "completed" and len(cache) == 2
    old = deepcopy(previous["payload"])
    forged = validate_fact(material_candidate(repo), definition, repo, "A", rule_version=BINDING_RULE_VERSION,
                           item_bindings=frozen)
    old["facts"].append(forged)
    old["needs_review"] = True
    provider = MaterialProvider([])
    instance = DynamicAgent(repo, provider, definition, item_bindings=frozen, previous_payload=old,
                            cache_load=lambda key, digest: cache.get((key, digest)))
    original_read, reads = instance._read, []

    def read(refs, budget):
        reads.append(list(refs))
        return original_read(refs, budget)

    instance._read = read
    result = instance.ask("重审并保留人工事实")
    assert result["status"] == "partial" and result["error"] == "manual_alternative_revalidation_required"
    assert instance._report == old
    assert [*forged["evidence_refs"], *forged["material_sources"][0]["evidence_refs"]] not in reads
    assert any(event.get("error") == "report_invalid" for event in result["events"])
    assert not provider.requests


def test_successful_semantic_repair_caches_complete_material_candidate():
    sample = material_sample()
    raw = material_candidate(sample[1])
    provider = MaterialProvider([raw | {"unit": "错误单位"}], repair=[raw])
    cached = []
    result = run(sample, provider, cache_save=lambda key, digest, data: cached.append(deepcopy(data)))
    assert result["status"] == "completed" and len(provider.requests) == 2
    assert len(cached) == 1 and cached[0]["facts"][0]["material_sources"] == raw["material_sources"]
    assert "material_binding_entry_digests" not in cached[0]["facts"][0]
    assert sum(bool(event.get("semantic_repair")) for event in result["events"]) == 1


def test_semantic_repair_may_add_material_groups_while_preserving_original_relationship():
    sample = separate_document_sample()
    repo = sample[1]
    claim = repo.blocks[1]
    sources = [{"evidence_refs": [block.evidence_id], "quote": block.text} for block in repo.blocks[2:]]
    repaired = {"criterion_id": "metric", "item_id": "sample", "supplier": "A", "value": True,
                "unit": "", "evidence_refs": [claim.evidence_id], "quote": claim.text,
                "material_sources": sources}
    raw = repaired | {"unit": "错误单位", "material_sources": sources[:1]}
    provider = MaterialProvider([raw], repair=[repaired])
    result = run(sample, provider)
    assert result["status"] == "completed" and len(provider.requests) == 2
    assert result["payload"]["facts"][0]["material_sources"] == sources
    assert "repair_missing_sources" not in result["payload"]["batch_coverage"][0]


def test_material_supplier_mismatch_uses_existing_single_schema_repair():
    sample = material_sample()
    raw = material_candidate(sample[1])
    provider = MaterialProvider([raw | {"supplier": "another"}], repair=[raw])
    result = run(sample, provider)
    assert result["status"] == "completed" and len(provider.requests) == 2
    assert sum(bool(event.get("structured_repair")) for event in result["events"]) == 1
    assert not any(event.get("semantic_repair") for event in result["events"])
    assert provider.schemas[0] == provider.schemas[1] == _facts_schema("A", material_sources=True)


@pytest.mark.parametrize("version", ["dynamic-rules-v10", "dynamic-rules-v9", "dynamic-rules-v8"])
def test_old_bound_rule_versions_keep_original_schema_and_reject_material_fields(version):
    definition, repo, frozen = material_sample()
    provider = MaterialProvider([material_candidate(repo)])
    instance = DynamicAgent(repo, provider, definition, item_bindings=frozen)
    instance.rule_version = version
    result = instance.ask("按旧版审查")
    assert result["status"] == "partial" and result["error"] == "invalid_output"
    assert len(provider.requests) == 2
    assert all(schema == _facts_schema("A") for schema in provider.schemas)


def test_unbound_review_keeps_original_schema_and_prompt_contract():
    definition, repo, _ = material_sample()
    raw = {key: value for key, value in material_candidate(repo).items() if key != "material_sources"}
    provider = MaterialProvider([raw])
    result = DynamicAgent(repo, provider, definition).ask("审查")
    assert result["status"] == "completed"
    assert result["payload"]["rule_version"] == "dynamic-rules-v7"
    assert provider.schemas == [_facts_schema("A")]
    assert "material_sources" not in provider.requests[0][0].content


def test_old_semantic_repair_prompt_does_not_offer_material_source_fields():
    definition, repo, _ = material_sample()
    raw = {key: value for key, value in material_candidate(repo).items() if key != "material_sources"}
    provider = MaterialProvider([raw | {"unit": "错误单位"}], repair=[raw])
    result = DynamicAgent(repo, provider, definition).ask("审查")
    assert result["status"] == "completed" and len(provider.requests) == 2
    assert all("material_sources" not in message.content for message in provider.requests[1])
