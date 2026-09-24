"""Produce an evidence index from the three synthetic development documents."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .parsing import parse_pdf, parse_xlsx


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mineru-executable", required=True, help="Path to mineru-kit executable")
    parser.add_argument("--output", required=True, type=Path, help="Evidence index JSON path")
    args = parser.parse_args()

    pack = Path(__file__).resolve().parents[2] / "examples" / "dev-pack-001"
    mineru_command = [args.mineru_executable]
    documents = [
        parse_pdf(pack / "requirements.pdf", document_id="requirements.pdf", mineru_command=mineru_command),
        parse_xlsx(pack / "vendor-a-quote.xlsx", document_id="vendor-a-quote.xlsx"),
        parse_pdf(pack / "vendor-a-spec.pdf", document_id="vendor-a-spec.pdf", mineru_command=mineru_command),
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps({"documents": [asdict(document) for document in documents]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        f"Wrote {len(documents)} documents and "
        f"{sum(len(document.blocks) for document in documents)} evidence blocks to {args.output}"
    )


if __name__ == "__main__":
    main()
