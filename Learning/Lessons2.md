# Lessons — Job Application Department build session (2026-08-10)

Distilled from the conversation where we fixed `career_watcher.py`, built `log_consolidator.py` and `intake_agent.py`, and worked through the underlying agent-design concepts using this codebase as the running example.

## Workflows vs. agents

Anthropic's own taxonomy splits agentic systems into two shapes:
- **Workflows** — a fixed, predefined sequence of LLM calls. Step A's output feeds step B, always in the same order, no branching.
- **Agents** — the LLM dynamically decides its own next action, in a loop, based on what it observes.

Most of what's in this project is a workflow, specifically the simplest kind (**prompt chaining**): `career_watcher.py` → `intake_agent.py` is a straight line. `agent.py`'s `/grill-me` flow, where the LLM decides in-conversation whether to call `save_candidate_fact`, is closer to a true agent. Neither is "better" — match the pattern to whether the task actually has a fixed shape or needs the model to choose its own path.

## The four types of agent memory (CoALA), mapped to this project

- **Working memory** — the active context for one run. `agent.py`'s `SQLiteSession` plays this role, but notice it's scoped to one process: a fresh `conversation_id` is generated every run, so nothing is ever revisited across sessions even though the underlying storage is technically persistent.
- **Semantic memory** — facts about the world, meant to be retrieved and grounded against. `facts.md`, `career_inferences.md`, and `profile.md` are this — not the *instructions* string, which is procedural, not semantic.
- **Procedural memory** — how to do things. Splits into *implicit* (baked into the model's weights, not inspectable) and *explicit* (`skills.md`'s written-out protocols, which you can read and edit).
- **Episodic memory** — specific past experiences. `logs/*.md` is this, but it was **write-only** until this session: nothing ever read it back in. `log_consolidator.py` builds the missing retrieval half — turning raw transcripts into a condensed digest `agent.py` actually loads on startup.

## Evals: three flavors, in order of reliability

1. **Self-critique** — same call asks itself "did I get this right?" Weakest: it's using the same judgment that produced the output, so it shares the same blind spots.
2. **Separate evaluator pass** — a second, narrowly-scoped call, given the source material and the output side by side, whose only job is checking specific criteria. Much stronger, because it's not defending its own prior work.
3. **Deterministic code check** — no LLM at all, for anything that doesn't require semantic judgment (e.g. "does this line contain a hedge word and a citation?").

The test for which one applies: *does checking this require judgment that depends on meaning, or is it a fixed procedure?* Judgment → LLM (ideally a separate pass). Fixed procedure → code.

## The smoke-detector / decision-maker split

A recurring confusion this session: conflating *noticing something changed* with *deciding what to do about it*. A smoke detector only notices smoke and beeps — it doesn't call the fire department. In this codebase: "did new files show up in `Career/`" is a cheap, dumb, no-judgment check (a timestamp comparison) that can run automatically every time. "Should I actually spend real LLM calls processing them" is a judgment call, and belongs to whatever's making the decision — not baked unconditionally into a startup script. This is the same underlying test as the evals heuristic above, applied to *triggering* instead of *checking*.

## Shared sessions = multi-agent dialogue, almost for free

Read the actual `SQLiteSession` source rather than assuming: history is stored keyed **only by a plain `session_id` string** — there's no concept of "agent" anywhere in the storage layer. That means pointing multiple *different* `Agent` objects at the same session object lets each one see everything the others said, automatically — no manually threading a growing prompt by hand. This is exactly how `intake_agent.py`'s five-stakeholder panel works: five distinct personas, one shared session, each round-2 response genuinely reacting to what the other four said in round 1 (verified empirically, not just assumed — round-2 outputs explicitly referenced "the panel" and added things beyond their own round-1 output).

## Real stakeholders beat generic categories

First design for the panel was skill-category agents (technical / soft / business) — but agents sorting facts into non-overlapping buckets have no reason to actually disagree with each other. Real stakeholder roles (Recruiter, Hiring Manager, Technical Lead, HR, Executive) have genuinely different priorities that can conflict on the *same* fact — that's what makes a dialogue actually useful instead of three parallel monologues.

## Streaming isn't automatic just because you call a streaming method

Every agent in this codebase originally did this:
```python
result = ws.run_streamed(agent, prompt)
async for event in result.stream_events():
    pass          # <- drains and discards every event
print(result.final_output)   # <- only shows the complete result, all at once
```
Calling `run_streamed` doesn't mean you're using the stream — it means the *option* exists. The actual incremental output lives in `RawResponsesStreamEvent` events wrapping `response.output_text.delta` chunks; you have to check for that event type and print `.delta` as it arrives. This bug was copied forward into three new files before being caught, which is its own lesson: an unquestioned pattern doesn't get more correct by being repeated.

## MCP: what it actually solves, and when it doesn't matter

The real difference from `@function_tool` isn't the code shape (`@mcp.tool()` looks almost identical) — it's **where the tool lives and who can call it**. A `@function_tool` only exists inside one Python process, callable only by `Agent` objects from that one SDK. An MCP server is a standalone process speaking one standard protocol, callable by *any* compliant client — Claude Desktop, Claude Code, this project's own `agent.py`, anything. The industry-standard value is the same reason USB won: write the tool once, use it anywhere, instead of a separate integration per framework. Bonus: clients *discover* an MCP server's tools live (`list_tools()`), so adding a tool to the server later doesn't require updating every client by hand — with `@function_tool`, a human has to add it to `tools=[...]` manually, everywhere it's used.

The honest tradeoff: this value is amortized across multiple consumers. For a tool with exactly one consumer (`save_candidate_fact`, called only by Job Getter today), `@function_tool` is genuinely simpler — no separate process, no transport layer. MCP earns its cost specifically when something *else* also needs to call the same tool.

## The recurring practice underneath all of this

Verify SDK/library behavior against actual source (or a live search) before asserting how it works, rather than answering from general/trained knowledge. This mattered concretely, more than once: confirming `SQLiteSession` really does share history across different agents (it does — read the source), confirming what a raw streaming event actually looks like before writing code against it, and confirming `mcp` was already installed before recommending a `pip install` that wasn't needed. A plausible-sounding technical claim and a verified one look identical until they're wrong.
