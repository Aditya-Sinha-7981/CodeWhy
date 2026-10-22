"""Ask newcomer questions about a project under three contexts, for blind scoring by a person.

    python eval/run_eval.py --project codewhy [--provider gemini --model <id>] [--yes]
    python eval/run_eval.py --summarize eval/runs/<timestamp>     # after scoring the sheet

Conditions (the same question, the same model, only the context changes):

    a  file listing only (every discovered path)
    b  raw concatenated source of every supported file (skipped, with a note,
       when it does not fit the provider's prompt budget)
    c  the generated PROJECT_DOCS.md named in eval/corpus.toml

Each answer comes from `generate_json` with an {"answer": string} schema. Output
goes to eval/runs/<UTC timestamp>/ (gitignored):

    answers.jsonl   every prompt's condition, context size and answer
    sheet.csv       shuffled rows WITHOUT the condition; fill `score` with
                    correct / partly / wrong, comparing against ground_truth
    key.csv         item -> condition (do not look at it while scoring)

`--summarize` joins a scored sheet with its key and prints the results table for
eval/RESULTS.md. Ground truth is written by a person from the code; questions
still marked TODO(user) are asked but cannot be scored.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
import tomllib
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

from codewhy.config import resolve_config
from codewhy.ignore import discover
from codewhy.llm.budget import estimate_tokens, fits, prompt_budget
from codewhy.llm.provider import ProviderError, build_provider

EVAL = Path(__file__).resolve().parent
REPO = EVAL.parent
ANSWER_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string", "description": "The answer in 1-4 sentences."}},
    "required": ["answer"],
}
CONDITIONS = {"a": "file listing", "b": "raw source", "c": "PROJECT_DOCS.md"}
SCORES = ("correct", "partly", "wrong")
INSTRUCTIONS = (
    "You are helping a developer who is new to a codebase. Answer the question using only the "
    "context below. Name the specific files, functions or settings involved. If the context does "
    "not contain the answer, say that it does not, rather than guessing. Reply with a JSON object "
    '{"answer": "..."} only.'
)


def load_corpus() -> dict[str, dict]:
    data = tomllib.loads((EVAL / "corpus.toml").read_text(encoding="utf-8"))
    return {project["name"]: project for project in data.get("project", [])}


def load_questions(name: str) -> list[dict]:
    data = tomllib.loads((EVAL / "questions" / f"{name}.toml").read_text(encoding="utf-8"))
    return data.get("question", [])


def contexts(root: Path, docs: Path, config) -> dict[str, str]:
    found = discover(root, config)
    listing = "\n".join(sorted([f.path for f in found.files] + found.other_files + [s.path for s in found.skips]))
    source = "\n\n".join(f"=== {f.path} ===\n{f.text}" for f in found.files)
    return {"a": f"Files in the project:\n{listing}", "b": f"Source code of the project:\n\n{source}", "c": docs.read_text(encoding="utf-8")}


def prompt_for(context: str, question: str) -> str:
    return f"{INSTRUCTIONS}\n\n## Context\n\n{context}\n\n## Question\n\n{question}\n"


def run(project_name: str, overrides: dict[str, object], assume_yes: bool, seed: int) -> Path:
    corpus = load_corpus()
    if project_name not in corpus:
        sys.exit(f"unknown project {project_name!r}; known: {', '.join(sorted(corpus))}")
    entry = corpus[project_name]
    root = (REPO / entry["path"]).resolve()
    docs = (REPO / entry["docs"]).resolve()
    if not docs.is_file():
        sys.exit(f"{docs} does not exist; generate it first (see eval/README.md)")
    questions = load_questions(project_name)
    config = resolve_config(root, overrides)
    llm = config.llm
    budget = prompt_budget(llm.effective_context_tokens, llm.effective_max_output_tokens)
    texts = contexts(root, docs, config)
    sizes = {c: estimate_tokens(prompt_for(t, "")) for c, t in texts.items()}
    planned = [(q, c) for q in questions for c in CONDITIONS if fits(prompt_for(texts[c], q["text"]), budget)]
    print(f"{len(questions)} questions x {len(CONDITIONS)} conditions; context sizes (est. tokens): {sizes}; budget {budget}")
    print(f"{len(planned)} calls, ≈ {sum(sizes[c] for _, c in planned):,} input tokens")

    provider = build_provider(config)
    if provider.is_remote and not (assume_yes or llm.acknowledge_remote):
        sys.exit(f"{provider.endpoint_label} is remote: the project's source and docs will be sent there. Re-run with --yes.")
    provider.preflight()

    out = EVAL / "runs" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out.mkdir(parents=True)
    rows: list[dict] = []
    with (out / "answers.jsonl").open("w", encoding="utf-8") as answers:
        for question in questions:
            for condition in CONDITIONS:
                prompt = prompt_for(texts[condition], question["text"])
                record = {"project": project_name, "question_id": question["id"], "condition": condition, "context_tokens": sizes[condition]}
                if not fits(prompt, budget):
                    record["skipped"] = f"context ≈ {sizes[condition]:,} tokens does not fit the prompt budget ({budget:,})"
                else:
                    try:
                        reply = provider.generate_json(prompt, ANSWER_SCHEMA, max_output_tokens=llm.effective_max_output_tokens)
                        record["answer"] = str(reply.get("answer", "")).strip()
                    except ProviderError as exc:
                        record["error"] = f"{type(exc).__name__}: {exc}"
                answers.write(json.dumps(record, ensure_ascii=False) + "\n")
                print(f"{question['id']} {condition}: {'skipped' if 'skipped' in record else 'error' if 'error' in record else 'answered'}")
                if "answer" in record:
                    rows.append({**record, "question": question["text"], "ground_truth": question.get("ground_truth", "TODO(user)")})

    random.Random(seed).shuffle(rows)
    with (out / "sheet.csv").open("w", encoding="utf-8", newline="") as sheet, (out / "key.csv").open("w", encoding="utf-8", newline="") as key:
        sheet_writer = csv.writer(sheet)
        key_writer = csv.writer(key)
        sheet_writer.writerow(["item", "project", "question", "ground_truth", "answer", "score"])
        key_writer.writerow(["item", "question_id", "condition", "context_tokens"])
        for item, row in enumerate(rows, start=1):
            sheet_writer.writerow([item, row["project"], row["question"], row["ground_truth"], row["answer"], ""])
            key_writer.writerow([item, row["question_id"], row["condition"], row["context_tokens"]])
    (out / "meta.json").write_text(
        json.dumps({"project": project_name, "provider": llm.provider, "model": llm.effective_model, "context_tokens": sizes, "budget": budget, "seed": seed}, indent=2),
        encoding="utf-8",
    )
    print(f"wrote {out}")
    return out


def summarize(run_dir: Path) -> None:
    with (run_dir / "key.csv").open(encoding="utf-8") as handle:
        key = {row["item"]: row for row in csv.DictReader(handle)}
    tally: dict[str, Counter] = defaultdict(Counter)
    with (run_dir / "sheet.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            score = row["score"].strip().lower()
            tally[key[row["item"]]["condition"]][score if score in SCORES else "unscored"] += 1
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    print(f"| Condition | Context (est. tokens) | Correct | Partly | Wrong | Unscored |")
    print("|---|---|---|---|---|---|")
    for condition, label in CONDITIONS.items():
        counts = tally.get(condition, Counter())
        size = meta["context_tokens"].get(condition, "?")
        fitted = "" if condition in tally else " (not asked: did not fit)"
        print(f"| {condition}: {label}{fitted} | {size:,} | {counts['correct']} | {counts['partly']} | {counts['wrong']} | {counts['unscored']} |")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run or summarize the codewhy quality evaluation.")
    parser.add_argument("--project")
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--yes", action="store_true", help="acknowledge sending the project to a remote endpoint")
    parser.add_argument("--seed", type=int, default=7, help="shuffle seed for the scoring sheet")
    parser.add_argument("--summarize", type=Path, metavar="RUN_DIR")
    args = parser.parse_args(argv)
    if args.summarize:
        summarize(args.summarize)
        return 0
    if not args.project:
        parser.error("--project is required (or use --summarize)")
    run(args.project, {"llm.provider": args.provider, "llm.model": args.model}, args.yes, args.seed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
