"""Preview or run a bounded Step 5 extraction on the hash-verified dev pack."""

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, BaseMessage

from .evidence.repository import EvidenceRepository, load_case
from .extraction.memory import extract_memory
from .providers import ProviderError, configured_provider
from .rules.memory import evaluate_memory

PACK = Path(__file__).resolve().parents[2] / "examples" / "dev-pack-001"
SUPPLIER = "vendor-a"
MAX_REQUESTS = 3
EXPECTED_BLOCKS = 35


def _preview_batches(repo: EvidenceRepository) -> list[list[dict]]:
    batches: list[list[dict]] = []

    def collect(operation: str, messages: list[BaseMessage], schema: dict) -> AIMessage:
        batches.append(json.loads(messages[-1].content)["evidence"])
        return AIMessage(content="", tool_calls=[{
            "name": "MemoryCandidates", "args": {"facts": []}, "id": "preview"
        }])

    extract_memory(repo, SUPPLIER, collect)
    return batches


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--send", action="store_true", help="Send three synthetic evidence batches to the configured model")
    args = parser.parse_args(argv)
    try:
        repo = load_case(PACK)
        batches = _preview_batches(repo)
    except (OSError, ValueError, KeyError):
        print("extract_probe_failed: case_input_invalid", file=sys.stderr)
        return 2

    block_count = sum(len(batch) for batch in batches)
    if not args.send:
        print(json.dumps({"mode": "preview", "supplier": SUPPLIER,
                          "model_requests": len(batches), "evidence_blocks": block_count},
                         ensure_ascii=False))
        return 0
    if len(batches) != MAX_REQUESTS or block_count != EXPECTED_BLOCKS:
        print("extract_probe_failed: dev_pack_changed", file=sys.stderr)
        return 2

    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    requests_started = 0
    input_tokens = output_tokens = 0

    try:
        provider = configured_provider()

        def model_call(operation: str, messages: list[BaseMessage], schema: dict) -> AIMessage:
            nonlocal requests_started, input_tokens, output_tokens
            if requests_started >= MAX_REQUESTS:
                raise ValueError("request_limit")
            requests_started += 1
            response = provider.extract(messages, schema, timeout=30, max_output_tokens=512)
            usage = response.usage_metadata or {}
            input_tokens += usage.get("input_tokens") or 0
            output_tokens += usage.get("output_tokens") or 0
            return response

        fact_set = extract_memory(repo, SUPPLIER, model_call)
        check = evaluate_memory(repo, SUPPLIER, list(fact_set.facts), scan_complete=fact_set.complete)
    except ProviderError as exc:
        print(f"extract_probe_failed: {exc.code}; requests_started={requests_started}", file=sys.stderr)
        return 2
    except (ValueError, KeyError, TypeError):
        print(f"extract_probe_failed: invalid_extraction; requests_started={requests_started}", file=sys.stderr)
        return 2

    facts = []
    for fact in fact_set.facts:
        block = repo.read(list(fact.evidence_refs))[0]
        location = (f"{block.sheet}!{block.cell}" if block.sheet else
                    f"PDF page {block.page_idx + 1}" if block.page_idx is not None else None)
        facts.append({"role": fact.role, "value": str(fact.value), "unit": fact.unit,
                      "source": block.document_id, "location": location})
    print(json.dumps({"mode": "send", "supplier": SUPPLIER,
                      "model_requests": requests_started, "input_tokens": input_tokens,
                      "output_tokens": output_tokens, "complete": fact_set.complete,
                      "processing_status": check.processing_status,
                      "business_status": check.business_status, "facts": facts},
                     ensure_ascii=False))
    return 0 if check.business_status == "资料冲突" else 1


if __name__ == "__main__":
    raise SystemExit(main())
