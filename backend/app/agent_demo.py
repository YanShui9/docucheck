"""Run a real configured Agent on a hash-verified synthetic document pack."""
import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv

from .agent.graph import AgentSession
from .evidence.repository import load_case
from .providers import ProviderError, configured_provider


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case-dir", type=Path, default=Path("examples/dev-pack-001"))
    parser.add_argument("--provider", choices=["configured"], default="configured")
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--question")
    parser.add_argument("--output", type=Path, default=Path("data/agent-demo"))
    args = parser.parse_args(argv)
    if not args.interactive and not args.question:
        parser.error("use --question or --interactive")
    # Explicit project-local configuration. Does not search parent directories.
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    try:
        provider = configured_provider()
        repo = load_case(args.case_dir)
    except ProviderError as exc:
        print(f"Model configuration failed: {exc.code}. Set DOCUCHECK_MODEL_* in local .env.", file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError):
        print("Case input invalid: check manifest, hashes and supported files.", file=sys.stderr)
        return 2
    session = AgentSession(repo, provider)
    print("DocuCheck M1：内存核验与来源追问。当前读取经哈希核验的模拟包解析缓存。")
    print("问题及必要资料片段会发送到已配置模型服务。输入 /exit 或 exit 结束。")
    exit_code = 0
    while True:
        try:
            question = input("问题> ") if args.interactive else args.question
            if question.strip().casefold() in {"/exit", "exit"}:
                break
            if not question.strip():
                if args.interactive:
                    continue
                return 2
            result = session.ask(question)
            args.output.mkdir(parents=True, exist_ok=True)
            path = args.output / f"{result.context.run_id}.json"
            path.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(result.answer)
            print(f"状态：{result.status}；模型调用：{result.usage['model_calls']}；记录：{path}")
            if result.status in {"failed", "partial", "cancelled"}:
                exit_code = 1
        except (EOFError, KeyboardInterrupt):
            session.cancel()
            break
        except ValueError:
            print("Question is invalid or exceeds 2000 characters.", file=sys.stderr)
            exit_code = 2
        if not args.interactive:
            break
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
