# Lessons — Job Application Department build session (2026-08-17)

This session opened as a pure concept discussion about tracing, then moved into auditing `intake_agent.py` at the user's request — which turned up that it was dead code, pointed at files that never existed, and had been quietly superseded by a second, undocumented pipeline. The rest of the session redesigned it into a structured, multi-round stakeholder panel: independent first commitments, a formal objection schema, and termination based on when reasoning stabilizes rather than when everyone agrees. Written to be read cold, with no memory of this conversation.

---

## Part 1: Tracing — the concept, and confirming it's already running for free

Opened as a pure concept question, unconnected to any code yet, but worth recording since it's a fact about the SDK this whole project runs on.

- The three legs of observability answer different questions: metrics = aggregate health, logs = discrete unconnected events, tracing = the connected story of one execution start to finish.
- A **trace** is one full journey (e.g., one call to `generate_inferences()`); a **span** is one step inside it (e.g., one `ws.run_streamed(...)` call), and spans nest into a tree via parent/child relationships propagated through the whole run — that's what lets you reconstruct "what actually happened" after the fact instead of guessing from scattered print statements.
- Confirmed: the OpenAI Agents SDK (`agents`, imported throughout this project) ships tracing **on by default**. Every run gets automatically wrapped in a `Trace`, built from typed `Span`s (agent span, generation/LLM-call span, function/tool-call span, handoff span, guardrail span, plus a generic custom span), and — unless you swap the processor — shipped straight to the OpenAI dashboard where the whole span tree for a run is visible. Traces can be grouped under one `group_id` to bucket multiple runs together (e.g. every turn of one conversation).
- Practical implication for this project, not yet checked: every `ws.run_streamed(...)` call in `intake_agent.py`, `career_watcher.py`, and `agent.py` may already be generating dashboard-visible trace data, with zero lines written for it. Worth actually looking at the dashboard before assuming tracing needs to be built here — it might already exist and just never have been looked at.

---

## Part 2: `intake_agent.py` had gone fully stale — a second pipeline grew up around it, undocumented

Lessons3 Part 4 documented a plan: `career_watcher.py` would become purely mechanical (just capture raw document text into `facts.md`), and `intake_agent.py` would be where all real semantic judgment happens, consuming that `facts.md`. That plan was never built. Instead, by this session, `career_watcher.py` had been further evolved — undocumented, with no update to Lessons2/3 reflecting it — into having its own real judgment-making agent again (`career_watcher_agent`, connected via MCP to `cw_mcp_server.py`), whose `save_career_inference` tool wrote directly and *finally* to `career_inferences.md`. `facts.md` was never touched by anything. `intake_agent.py` itself was never imported or called anywhere (confirmed by grepping the whole directory for `import intake_agent`) — and its own `FACTS_FILE`/`INFERENCES_FILE` constants pointed at `Career\facts.md` and `Career\career_inferences.md`, neither of which has ever existed; the real files live at the top level of the folder.

So `intake_agent.py` wasn't almost-working — it was structurally guaranteed to no-op even if someone had called it (`generate_inferences()` immediately hits `if not os.path.exists(FACTS_FILE): return`), sitting alongside a completely different, working pipeline that had quietly taken over its intended job.

This session's fix: `cw_mcp_server.py`'s tool was renamed `save_career_inference` → `save_candidate_fact` and repointed to write `facts.md` instead of `career_inferences.md`; `intake_agent.py`'s paths were corrected to the real top-level files; and `generate_inferences()` was wired into `agent.py`'s startup sequence, right after `career_watcher(...)`, so the pipeline is now genuinely `career_watcher` (first pass) → `facts.md` → stakeholder panel → `career_inferences.md`, matching what the user actually wanted ("career_watcher make a first pass that lets intake_agent have an informed conversation").

General lesson, same shape as Lessons3 Part 7 but at a larger scale: it isn't just a stray comment that goes stale when code changes out from under it — an entire documented architecture decision can be silently superseded by later, undocumented work, and nothing forces the earlier design notes to catch up. The only way this surfaced was checking actual current imports and actual file paths against what the code claimed, not trusting either the code's own apparent intent or the prior session's write-up.

---

## Part 3: the shared-session bug, root-caused precisely this time

Lessons2 described shared `SQLiteSession` objects as a feature: point multiple `Agent`s at the same session and each sees everything the others said, for free, no manual prompt-threading. This session found the same mechanism causing real damage in a spot where independence actually mattered: `intake_agent.py`'s "round 1" was supposed to be five separate, uninfluenced first impressions, but all five stakeholders shared one session object — so by the time the second stakeholder ran, it already had the first stakeholder's answer sitting in its context, and so on down the panel. Only the very first stakeholder (Recruiter) was ever giving a genuinely independent read.

This session pinned the actual mechanism down more precisely than Lessons2 did, by reading `SQLiteSession`'s constructor directly rather than reasoning from the `session_id` string alone: in the default in-memory mode, *every* `SQLiteSession(...)` call opens a fresh, private `sqlite3.connect(":memory:")` connection. Isolation is per Python **object**, not per `session_id` string — two `SQLiteSession("same_name")` calls would still be two separate, unconnected databases. That means the fix isn't "give each stakeholder a distinct session_id" — it's "give each stakeholder a genuinely separate `SQLiteSession()` object," and merge explicitly once each has committed independently:

```python
solo_session = SQLiteSession(f"intake_{agent.name}_{timestamp}_r1")
...
await shared_session.add_items(await solo_session.get_items())
```

`get_items()`/`add_items()` round-trip through the SDK's own typed item format, so draining one session into another is a supported operation, not a hack.

---

## Part 4: the groupthink fix Lessons3 flagged but never built — built this session, with one loose end

Lessons3 Part 10 named the problem precisely: telling a panel it may "agree, disagree, or add something the others missed" permits disagreement without creating any actual pressure to find it, so panels drift toward polite consensus. It proposed a direction — require something adversarial before a round counts as complete — but nothing was implemented.

This session built three concrete mechanisms for it:

1. **A no-capitulation guardrail**, added to `COMMON_GUARDRAILS`: a stakeholder may only change position for an actual factual error, an unsupported leap in its own reasoning, or evidence it missed — never because of majority disagreement alone.
2. **A formal objection schema** (Pydantic `Objection`): every objection must name a `target_stakeholder`, a `target_claim`, one of four `objection_type` categories (`factual_error` / `unsupported_inference` / `omitted_consideration` / `scope_violation`), and `evidence`. Forcing an objection into this shape makes a vague "I sort of disagree" harder to produce than a specific, checkable claim.
3. **Termination on stability, not agreement**: the panel now runs multiple reaction rounds (capped at `MAX_ROUNDS = 4`) and stops only once a round produces zero new objections from anyone, self-reported per-objection via `is_new_this_round`. Genuine, unresolved disagreement is allowed to survive all the way to the end.

**Loose end, caught but not yet fixed as of this write-up:** the Mediator's instructions were supposed to stop rewarding convergence, since that directly undercuts point 3 above — real disagreement needs to survive to the final output, not get smoothed away. The new instructions do add "report genuine, unresolved disagreement explicitly as a DISPUTED item, don't fold it into consensus" — but the *old* line "focus on where the panel's perspectives actually converge" is still sitting right above it, unremoved, in direct tension with the new instruction. Caught during a self-review this session; a fix was proposed and declined in the moment, so it's still sitting in the code as of this write-up. Worth remembering: a plan correctly identifying a conflict doesn't guarantee the implementation actually removes the conflicting half — check the diff, not just the plan.

---

## Part 5: structured output (Pydantic `output_type`), from first principles

This session used the Agents SDK's structured-output support for the first time in this project — worth recording the mechanics, since Lessons2/3 never touched it.

- A Python **type hint** (`text: str`) is just a note for humans/tools by default — it doesn't stop you from putting the wrong kind of value there.
- `Enum` (fixed set of *named* options, e.g. `ObjectionType`) and `typing.Literal` (fixed set of *inline* string options, e.g. `hedge: Literal["likely", "plausible", "may indicate", "clear"]`) both restrict a field to an exact set of values — same restriction, different ceremony. Use `Enum` when the type has its own identity and gets reused elsewhere; `Literal` for a one-off restriction on a single field.
- Pydantic's `BaseModel` is a Python class (i.e., genuinely object-oriented — `class Objection(BaseModel)` is inheritance, no different in kind from any other OOP class) that adds *enforced* type-checking on top of plain type hints, and — critically for this use case — can describe its own shape as a JSON schema automatically.
- That schema is what makes this useful for talking to an LLM at all: handing a `BaseModel` subclass to `Agent(..., output_type=SomeModel)` (or `agent.clone(output_type=SomeModel)` — `.clone()` does a `dataclasses.replace`, so swapping the output type per round doesn't require re-writing an agent's instructions) forces the model's reply to conform to that exact shape. `Field(description=...)` isn't just documentation — those strings get embedded in the schema sent to the model, functioning as inline instructions for what each field should contain.
- `result.final_output` becomes the parsed model instance instead of a plain string once `output_type` is set — same attribute, different type depending on how the agent was configured.
- Wrinkle: `utils.stream_and_print` prints raw text deltas as they arrive, which is exactly right for a plain string reply but turns into unreadable partial JSON once `output_type` is set. Rather than change the shared `stream_and_print` (which `agent.py`'s live chat loop genuinely needs streaming prose from), `intake_agent.py` got its own small local helper that drains the stream silently and returns the parsed object, letting the caller print whichever field is actually meant for human eyes:

```python
async def _run_structured(result_stream):
    async for _ in result_stream.stream_events():
        pass
    return result_stream.final_output
```

---

## Part 6: a schema can be both machine-readable and human-readable — that's not actually a tradeoff

Initial framing presented round 1's output format as a choice: structured (`Claim` objects — matchable, but loses natural hedging prose) *or* free text (readable, but round 2 has to paraphrase what it's objecting to from memory). The correct challenge to that framing — "I wish it could be both?" — was right: a Pydantic schema can hold a free-text field and structured fields side by side in the same object:

```python
class StakeholderInitialRead(BaseModel):
    narrative: str        # natural-language reasoning, in the stakeholder's own voice
    claims: list[Claim]   # discrete claims extracted from that narrative
```

The model has to write the natural version *and* distill it into discrete objects in the same response — which is generally more reliable than trying to parse structure back out of free text after the fact. General principle: "structured vs. readable" is often a false binary once you're defining the schema yourself — give each concern its own field instead of picking one.

---

## Part 7: sanity-check combinatorial claims the same way you'd verify an SDK claim

Lessons2 closed on "verify SDK/library behavior against actual source before asserting how it works." This session hit the same failure mode in a different shape: a purely arithmetic claim, not a library-behavior claim. A question presented to confirm model assignment offered an option worded as "all 5 stakeholders get distinct models; Mediator reuses one of the 4" — which is impossible on its face, since 5 distinct values can't be drawn from a pool of 4 regardless of what the Mediator does. The error wasn't caught until re-examining the question after it had already been answered.

Confirmed separately: the installed `gpt-5.6-*` model family has exactly four non-dated variants (`gpt-5.6`, `-sol`, `-terra`, `-luna`, found via the regex patterns in `agents/models/default_models.py`). Final resolution, decided explicitly once the real constraint was stated correctly: Recruiter, Hiring Manager, and Technical Lead each get a distinct variant; HR and Executive share the fourth; the Mediator reuses one of the four as well (it synthesizes rather than argues a position, so distinctness matters less for it specifically).

The general lesson: "verify before asserting" isn't only about checking library source — it applies equally to checking your own quantitative/combinatorial claims (does this count of options actually fit this count of slots?) before presenting them as a settled premise.
