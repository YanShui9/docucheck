"""Small synthetic provider smoke test; preview never contacts a model."""

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from .agent.dynamic import DynamicAgent
from .criteria.models import ChecklistDefinition
from .evidence.models import Document
from .evidence.repository import EvidenceRepository, digest
from .parsing import EvidenceBlock, ParseResult
from .providers import configured_provider
from .providers.base import ProviderError

MAX_CALLS = 6


def fixtures():
    documents = []
    for name, role, supplier, text in [
        ("synthetic-buyer.xlsx", "requirement", None, "苹果 数量不低于10箱"),
        ("synthetic-vendor.xlsx", "quote", "示例供应商", "苹果 数量12箱"),
    ]:
        block = EvidenceBlock("1", name, text, sheet="采购资料", cell="A1")
        parsed = ParseResult(
            name,
            "synthetic-v1",
            "complete",
            (block,),
            total_sheets=1,
            processed_sheets=("采购资料",),
        )
        documents.append(Document(name, digest(text), role, supplier, None, parsed))
    repo = EvidenceRepository("synthetic-probe", 1, documents)
    definition = ChecklistDefinition.model_validate(
        {
            "items": [{"item_id": "apple", "name": "苹果"}],
            "criteria": [
                {
                    "criterion_id": "quantity",
                    "item_id": "apple",
                    "name": "数量",
                    "rule": {
                        "type": "numeric",
                        "operator": "gte",
                        "value": "10",
                        "unit": "箱",
                    },
                    "source": {
                        "kind": "user_defined",
                        "reason": "探针显式预设的模拟要求",
                    },
                }
            ],
        }
    )
    return repo, definition


class CappedProvider:
    def __init__(self, provider):
        self.provider = provider
        self.context_window = provider.context_window
        self.model_id = getattr(provider, "model_id", None)
        self.run_max_input_tokens = getattr(provider, "run_max_input_tokens", None)
        self.run_max_output_tokens = getattr(provider, "run_max_output_tokens", None)
        self.context_safety_percent = getattr(provider, "context_safety_percent", 5)
        self.calls = 0

    def _call(self, method, *args, **kwargs):
        if self.calls >= MAX_CALLS:
            raise ProviderError("probe_call_limit")
        self.calls += 1
        return getattr(self.provider, method)(*args, **kwargs)

    def decide(self, *args, **kwargs):
        return self._call("decide", *args, **kwargs)

    def extract(self, *args, **kwargs):
        return self._call("extract", *args, **kwargs)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--workflow", choices=("review", "proposal"), default="review")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.output and args.output.exists():
        print(json.dumps({"error": "report_exists"}))
        return 2
    repo, definition = fixtures()
    proposal = args.workflow == "proposal"
    if proposal:
        repo = EvidenceRepository(
            repo.case_id,
            repo.revision,
            [d for d in repo.documents if d.role == "requirement"],
        )
    report = {
        "mode": "send" if args.send else "preview",
        "workflow": args.workflow,
        "data": "synthetic_only",
        "documents": [repo.source_name(d.document_id) for d in repo.documents],
        "source_text": [b.text for b in repo.blocks],
        "max_model_requests": MAX_CALLS,
        "writes_business_data": False,
        "confirms_user_checklist": False,
    }
    code = 0
    if args.send:
        load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
        try:
            provider = CappedProvider(configured_provider())
            result = DynamicAgent(repo, provider, None if proposal else definition).ask(
                "从采购需求原文提出审查指标候选，等我确认"
                if proposal
                else "请完整审查示例供应商数量，并说明来源",
                operation="propose_checklist" if proposal else "review",
            )
            report.update(
                status=result["status"],
                answer=result["answer"],
                error=result["error"],
                usage=result["usage"],
                model_requests=provider.calls,
                checks=result["payload"]["checks"],
                definition=result["payload"].get("definition"),
                needs_resolution=result["payload"].get("needs_resolution"),
            )
            code = 0 if result["status"] == "completed" else 2
        except ProviderError as exc:
            report.update(status="failed", error=exc.code)
            code = 2
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
