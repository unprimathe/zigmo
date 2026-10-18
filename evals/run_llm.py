"""LLM eval: can Claude answer each business question using only the zigmo MCP tools?

Starts the real MCP server over stdio, exposes its tools to Claude, and runs a manual
tool-use loop per question. A question passes when the rows of Claude's last successful
query_metrics call match the oracle. Costs money; not part of CI.

Usage:
    uv run --extra llm python evals/run_llm.py [--only ID ...]
Requires Anthropic credentials (ANTHROPIC_API_KEY or an `ant auth login` profile).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import anthropic
import duckdb
import yaml
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from zigmo.engine import DEFAULT_DB

sys.path.insert(0, str(Path(__file__).parent))
from run_deterministic import QUESTIONS, rows_match

MODEL = os.environ.get("ZIGMO_EVAL_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("ZIGMO_EVAL_EFFORT", "medium")
MAX_TURNS = 8
SYSTEM = (
    "You are a business analyst. Answer the user's question using only the zigmo tools. "
    "Start with list_metrics if you need the catalog. Reply with the number(s) first, "
    "then one short sentence of context. Never invent numbers."
)


async def ask(client: anthropic.Anthropic, session: ClientSession, tools: list[dict],
              question: str) -> dict:
    messages = [{"role": "user", "content": question}]
    calls, last_rows = [], None
    for _ in range(MAX_TURNS):
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM,
            tools=tools,
            messages=messages,
            output_config={"effort": EFFORT},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            return {"answer": None, "calls": calls, "rows": last_rows, "status": "refusal"}
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            text = "".join(b.text for b in response.content if b.type == "text")
            return {"answer": text, "calls": calls, "rows": last_rows, "status": "done"}

        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            out = await session.call_tool(block.name, block.input)
            payload = "".join(c.text for c in out.content if c.type == "text")
            calls.append({"tool": block.name, "input": block.input})
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                data = {}
            if block.name == "query_metrics" and "rows" in data:
                last_rows = data["rows"]
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": payload,
                            "is_error": bool(out.isError or "error" in data)})
        messages.append({"role": "user", "content": results})
    return {"answer": None, "calls": calls, "rows": last_rows, "status": "max_turns"}


async def main(only: set[str]) -> int:
    questions = [q for q in yaml.safe_load(QUESTIONS.read_text(encoding="utf-8"))
                 if not only or q["id"] in only]
    oracle = duckdb.connect(str(DEFAULT_DB), read_only=True)
    client = anthropic.Anthropic()
    server = StdioServerParameters(command=sys.executable, args=["-m", "zigmo.server"],
                                   env={**os.environ, "ZIGMO_AUDIT": "audit/llm-eval.jsonl"})
    report = [f"# zigmo LLM eval — {MODEL} (effort {EFFORT}) — {datetime.now():%Y-%m-%d %H:%M}",
              "", "| id | result | tool calls | answer |", "|---|---|---|---|"]
    passed = 0
    async with stdio_client(server) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = [{"name": t.name, "description": t.description or "",
                  "input_schema": t.inputSchema} for t in (await session.list_tools()).tools]
        for q in questions:
            want = [tuple(float(v) if hasattr(v, "as_tuple") else v for v in row)
                    for row in oracle.sql(q["oracle"]).fetchall()]
            r = await ask(client, session, tools, q["question"])
            ok = r["rows"] is not None and rows_match(r["rows"], want)
            passed += ok
            answer = (r["answer"] or r["status"]).replace("\n", " ").replace("|", "/")[:160]
            print(f"{'PASS' if ok else 'FAIL'}  {q['id']}  ({len(r['calls'])} calls)")
            report.append(f"| {q['id']} | {'✅' if ok else '❌'} | {len(r['calls'])} | {answer} |")
    report += ["", f"**{passed}/{len(questions)} passed**"]
    out = Path("evals/reports") / f"llm-{datetime.now():%Y%m%d-%H%M}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(report) + "\n", encoding="utf-8")
    print(f"\n{passed}/{len(questions)} passed — report: {out}")
    return 0 if passed == len(questions) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", nargs="*", default=[])
    sys.exit(asyncio.run(main(set(parser.parse_args().only))))
