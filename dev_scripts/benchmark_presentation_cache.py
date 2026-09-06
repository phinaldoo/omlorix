#!/usr/bin/env python3
"""Offline prefix/context comparison; no provider calls or user-data queries.

Uses the real presentation instructions, Canvas parameter schema, image-pruning
policy and conservative context estimator with synthetic HTML/edit receipts.
Reusable units are complete identical prefix blocks from any earlier request.
These are NOT provider tokens, measured cache hits, latency or billing costs.
"""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.llm.generation.context import ContextBuilder, ContextBudgetExceeded, estimate_tokens
from app.tools.canvas_markdown.schemas import canvas_parameters_schema
from app.tools.slide_presentation.system_instructions import get_sys_instruct_generate_html
from app.tools.subagents.session import SubagentSession


class OfflineSession(SubagentSession):
    def check_cancellation(self):
        # No cancellation registry/Redis access for synthetic, nonexecuting runs.
        pass


def prefix_units(request):
    # Model tools precede instructions/history in this OpenAI-style cache model.
    # Keep a sentinel even when definitions are absent: removal breaks prefix 0.
    return [
        {"tools": request.get("tools")},
        {"instructions": request["instructions"]},
        *request["input"],
    ]


def shared_units(left, right):
    total = 0
    for old, new in zip(left, right):
        if old != new:
            break
        total += estimate_tokens(new) + 4
    return total


def benchmark(slides, renders, window, policy):
    session = OfflineSession("offline-cache-benchmark", (), max_calls=renders)
    context = ContextBuilder(preserve_history=True)
    history = [{"role": "user", "content": "Follow the complete presentation brief. " * 50}]
    schemas = [{
        "type": "function", "name": "update_presentation",
        "parameters": canvas_parameters_schema(),
    }]
    instructions = get_sys_instruct_generate_html()
    previous, rows = [], []
    prune_events = 0
    for revision in range(renders + 1):
        if revision:
            ids = [f"review-{revision}-{sheet}" for sheet in range((slides + 3) // 4)]
            session.attachments.update({file_id: {} for file_id in ids})
            session.latest_attachment_ids = set(ids)
            html = "<section class='slide'>Synthetic slide content.</section>" * (slides * 15)
            arguments = (
                {"type": "html", "content": html}
                if revision == 1 else
                {"file_id": "deck", "expected_revision": revision - 1, "edits": [{
                    "start_snippet": "Synthetic slide content.",
                    "end_snippet": "Synthetic slide content.",
                    "content": f"Improved slide content {revision}.",
                }]}
            )
            history.extend([
                {"type": "function_call", "name": "update_presentation",
                 "call_id": str(revision), "arguments": json.dumps(arguments)},
                {"type": "function_call_output", "call_id": str(revision),
                 "output": json.dumps({"revision": revision, "calls_remaining": renders - revision})},
            ])
            for file_id in ids:
                history.append({"role": "user", "content": [
                    {"type": "input_text", "text": f"Metadata of the file: {file_id}"},
                    {"type": "input_image", "image_url": f"synthetic://{file_id}"},
                ]})
        request = {"tools": deepcopy(schemas), "instructions": instructions, "input": deepcopy(history)}
        session.calls = revision
        if policy != "retain":
            prune_events += int(session.prune_obsolete_attachments(request))
        session.prepare_request(request)
        if policy == "baseline" and revision == renders:
            request.pop("tools")
            request.pop("tool_choice")
        try:
            try:
                context.prepare(request, settings={"input_token_limit": window}, protocol="openai")
            except ContextBudgetExceeded:
                if not session.prune_obsolete_attachments(request):
                    raise
                prune_events += 1
                context.prepare(request, settings={"input_token_limit": window}, protocol="openai")
        except ContextBudgetExceeded:
            return {"policy": policy, "status": "context_exceeded", "revision": revision}
        units = prefix_units(request)
        reused = max((shared_units(old, units) for old in previous), default=0)
        previous.append(units)
        rows.append({
            "revision": revision,
            "input_units": sum(estimate_tokens(unit) + 4 for unit in units),
            "reusable_prefix_units": reused,
            "context_estimate": context.last_report["estimated_input_tokens"],
        })
    total = sum(row["input_units"] for row in rows)
    reused = sum(row["reusable_prefix_units"] for row in rows)
    return {
        "policy": policy, "status": "ok", "input_units": total,
        "reusable_prefix_units": reused, "nonreusable_units": total - reused,
        "reusable_percent": round(100 * reused / total, 1),
        "peak_context_estimate": max(row["context_estimate"] for row in rows),
        "prune_events": prune_events, "requests": rows,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slides", type=int, choices=range(1, 51), default=16, metavar="1..50")
    parser.add_argument("--renders", type=int, choices=range(1, 5), default=4, metavar="1..4")
    parser.add_argument("--window", type=int, default=262144)
    args = parser.parse_args()
    print(json.dumps({
        "measurement": "offline conservative estimate, not API token/cache statistics",
        **vars(args),
        "results": [benchmark(args.slides, args.renders, args.window, policy)
                    for policy in ("baseline", "prune_stable_tools", "retain")],
    }, indent=2))
