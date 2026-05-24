"""Run the research flow for the sample questions."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.cli import render_answer
from src.core.researcher import research_question
from src.offline import OfflineLLM, offline_fetchers


async def run(limit: int, offline: bool) -> None:
    root = Path(__file__).resolve().parents[1]
    question_file = root / "data" / "research_questions.json"
    questions = json.loads(question_file.read_text(encoding="utf-8"))["questions"][:limit]

    for item in questions:
        print("=" * 72)
        result = await research_question(
            item["text"],
            fetchers=offline_fetchers() if offline else None,
            llm=OfflineLLM() if offline else None,
        )
        print(render_answer(result))
        print()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--live", action="store_true", help="call real providers instead of offline fakes")
    args = parser.parse_args()
    asyncio.run(run(args.limit, offline=not args.live))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())