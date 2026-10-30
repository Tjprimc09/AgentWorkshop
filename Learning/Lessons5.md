# Lessons — Job Application Department debugging session (2026-08-17)

This session started from a bug report ("check career documents" reports "the review timed out," and Job Getter can't remember anything real about Tyler) and ended somewhere far more interesting than a timeout fix: the actual cause was a self-reinforcing false belief living in the agent's own memory system, not a technical fault anywhere in the code. Getting there required retracting a wrong diagnosis mid-session, building an actual reproduction ladder instead of reading more source, and then designing a fix that heals itself instead of patching the one bad file. Written to be readable cold, with no memory of this conversation.

---

## Part 1: The real bug — a self-reinforcing false-memory loop

### 1.1 The symptom

```
You: check career documents
Job Getter: I couldn't access the career documents because the review timed out.

You: What do you remember from past sessions?
Job Getter: I remember only that you're interested in career and job-search help...
No reliable details are known about your experience, education, skills...
```

This looked like two separate problems: a broken tool, and a broken memory. It was actually one problem, wearing two costumes.

### 1.2 The mechanism

`agent.py`'s `get_agent_instructions()` reads `logs/digest.md` and bakes it verbatim into Job Getter's system instructions on every run. At the time this session started, `digest.md` already contained:

```
- A prior attempt to access their career documents timed out; no documents or personal background were provided.
```

That single sentence, once baked into instructions, closes a loop:

1. Job Getter starts a new session already "knowing" (from its own instructions) that this tool doesn't work.
2. Asked to check career documents, it asserts failure — very plausibly without genuinely attempting the tool call and waiting, since it already "knows" the answer.
3. That assertion becomes part of the conversation transcript.
4. `log_consolidator.py` reads the transcript and folds it into the digest. Its own rule 2 says "don't just append — integrate," so it doesn't add a second copy of the claim; it **reinforces** the existing one.
5. The next session inherits the same claim, now with one more transcript's worth of apparent corroboration behind it.

Nothing in the actual code was ever the bottleneck. The system had talked itself into believing something false, and then kept re-confirming its own prior statement as evidence.

### 1.3 The two-sided fix

Fixing only the symptom (editing `digest.md` to remove the stale line) would not have prevented recurrence — the next time *any* tool call got mischaracterized, the same loop would start over. The actual fix touches both ends of the loop:

**Consumption side** (`agent.py`, `get_agent_instructions()`) — teach Job Getter that a summary of past sessions is not evidence about current system state:

```python
if digest_content.strip():
    instructions += (
        f"\n\nHere is a summary of past conversations:\n{digest_content}"
        "\n\nThis summary describes what happened in past sessions — it is not "
        "evidence about whether a tool works right now. If the current request "
        "calls for a tool (e.g. checking career documents), always attempt it "
        "fresh in this conversation rather than assuming it will fail because a "
        "past session reported a failure. Only report a tool as unavailable or "
        "failing if you actually called it in this conversation and it failed."
    )
```

**Production side** (`log_consolidator.py`'s instructions) — stop the digest from absorbing this category of claim in the first place:

```
4. Do not record tool failures, errors, or claims that a capability is
   "unavailable" / "timed out" as established facts. Those describe transient
   technical state at one point in time, not something true about the user or
   their situation — encoding them here just causes future sessions to wrongly
   assume something is still broken instead of trying it fresh.
```

Neither half alone is sufficient. The consumption-side fix stops Job Getter from *trusting* a bad claim already in the digest, but doesn't stop a fresh bad claim from entering later. The production-side fix stops new bad claims from being written, but does nothing about the one already sitting in `digest.md` when the session started. Together, they close the loop at both the point where a false claim could be written and the point where it could be acted on.

### 1.4 Why this fix is self-healing, and the manual fix wasn't chosen

The obvious-looking fix — just edit `digest.md` and delete the stale line — was deliberately *not* done. Once the guardrails above are in place, the next real "check career documents" request will succeed (proven in Part 2), producing a transcript where Job Getter actually got real results. `log_consolidator`'s existing rule 2 ("revise, don't leave both versions side by side") will then naturally overwrite the stale claim with the truth on its own. A manual edit fixes the data once; the guardrail fixes the *process that produces* the data, which is the actual defect.

### 1.5 The deeper pattern (ties back to the CoALA memory model)

Lessons2 mapped this project onto the four CoALA memory types — working, semantic, procedural, episodic — and noted `log_consolidator.py` as the missing piece that turns write-only episodic memory (`logs/*.md`) into retrievable semantic memory (`digest.md`). This session is the sharp edge of that same idea: **a compression step that turns raw history into a trusted summary can also compress a single bad inference into permanent "fact,"** because every future compression treats the current summary as a trusted starting point rather than re-deriving from the raw transcripts each time. The fix isn't "compress more carefully" — it's marking one whole *category* of claim (self-reported system/tool status) as unreliable-by-default at both the point it's produced and the point it's consumed, so no single bad turn can propagate forward unchecked.

---

## Part 2: Debugging by building a reproduction ladder, not by reading more source

### 2.1 The wrong-but-plausible first theory

The first diagnosis was that `client_session_timeout_seconds=600` (the MCP client's read timeout, set explicitly in `agent.py`, `career_watcher.py`, and `log_consolidator.py`) was being hit — a nested chain of nested MCP subprocess connections, one inside another, seemed like a plausible place for a 10-minute wait to silently blow past. A plan was written around this theory and a fix proposed: raise the timeout, add checkpointing.

The user's response cut straight through it: **"how did it time out if it didn't take 600 seconds to even do so?"** — a real-world observation (the failure came back fast) that the theory didn't explain. That's the moment to stop defending the theory and go verify, not to rationalize around the objection.

### 2.2 Tracing the actual SDK source before touching a line of the app

Rather than guessing at a different number, the installed `agents` and `mcp` package source got read directly:
- `client_session_timeout_seconds` governs the MCP `ClientSession` read timeout — correctly set to `600` everywhere in this codebase, and it governs *both* regular tool-call reads and the initial `session.initialize()` handshake.
- `MCPServerManager` has a separate `connect_timeout_seconds=10.0` default that looked like a strong candidate for a shorter, hidden timeout — but that class isn't used anywhere in this project. All three files connect via plain `async with MCPServerStdio(...)`, which only ever respects the 600s value already configured.
- The raw `mcp` package itself (not just the `agents` wrapper) was also checked for a hardcoded shorter timeout on `stdio_client`/`ClientSession.initialize()`. There isn't one.

Conclusion from source alone: **nothing in this stack is configured to time out in under 600 seconds.** That ruled the theory out on paper, but didn't yet explain what *was* happening — for that, the code needed to actually run.

### 2.3 The reproduction ladder

Each step below reproduces one more piece of the real chain than the last, isolating exactly which piece (if any) was the actual difference:

1. **Call `career_watcher()` directly, minimal prompt** ("just call `list_unprocessed_documents`, don't investigate") — succeeded instantly. Rules out: connection/handshake problems.
2. **Call `career_watcher()` directly, read one document** — succeeded in 8.3s. Rules out: `read_document`/PDF extraction being the bottleneck.
3. **Call `career_watcher()` directly, fully unconstrained** ("check career documents", ~46 tool calls) — succeeded in 26.7s, with real findings written to `career_inferences.md`. Rules out: workload size/depth being the bottleneck.
4. **Reproduce the real nested chain** — Job Getter's own connection to `mcp_server.py`, calling its `check_career_documents` tool, which spawns a *second* subprocess (`cw_mcp_server.py`) from inside the first one's own request handler — via `Runner.run`. Succeeded in 34.4s. Rules out: subprocess-spawned-from-inside-a-subprocess being fundamentally broken on this system.
5. **Reproduce the real execution path exactly** — swap `Runner.run` for the actual `responses_websocket_session()` / `ws.run_streamed()` combination `agent.py` really uses. Succeeded in 21.5s. Rules out: the websocket streaming layer having its own hidden timeout or blocking behavior.

Four consecutive full successes, each one closer to the literal real code path than the last, is what actually closed the case: the tool was never broken. Only after step 5 succeeded did the digest-poisoning theory (Part 1) become the remaining explanation — and it fit every piece of evidence, including why an *unrelated* subsystem (`log_consolidator`) reported the same "timed out" wording in the same real session.

### 2.4 The general lesson

When a bug resists explanation from reading code, and a plausible theory gets disproven by a real-world fact the user provides, the fastest path to ground truth is usually not "read more source" — it's build the smallest possible reproduction, confirm it, and then add back one piece of the real system at a time until something actually breaks (or, as here, until you've added back *everything* and it never does, which is itself the answer).

---

## Part 3: "Empty" doesn't mean "broken" — verify before fixing

Early in the session, `processed_files.json` was empty while `career_inferences.md` already had substantial real content. The natural inference: `save_career_inference` (called early/often per the skills doc) was getting through, but `mark_processed` (called only once a document is fully investigated) never was — a checkpoint bug, presumably because runs were getting cut off mid-investigation before reaching it. A fix was planned: move `mark_processed` earlier in the process, right after `read_document`, so progress survives an interrupted run.

That fix was never applied. After the reproduction runs in Part 2 actually completed successfully, `processed_files.json` was re-checked — and it now had 27 real entries. `mark_processed` had been working correctly all along, exactly per the existing instructions; it just had never gotten the chance to run to completion before, for the same underlying reason as Part 1 (the model believing the tool didn't work, so genuine attempts were rare or truncated). Moving `mark_processed` earlier would have solved a problem that didn't exist, and risked creating a real one: a document marked "done" before its facts are ever actually saved, if a run is genuinely interrupted mid-way.

The lesson: an empty or missing piece of state is evidence that *something* prevented it from being populated — but "the code that populates it is broken" and "the code that populates it never successfully ran" are different explanations, and only one of them is fixed by changing that code. Confirm which one you're looking at (by actually getting something to run to completion) before editing.

---

## Part 4: A diagnostic script has its own bugs — rule those out first

Building the reproduction ladder in Part 2 hit two dead ends that had nothing to do with the actual system:

```python
# First attempt at reproducing the real chain — created a fresh Agent()
# without ever loading environment variables:
probe_agent = Agent(name="Probe", model="gpt-5.6-luna", ...)
# -> openai.OpenAIError: Missing credentials... OPENAI_API_KEY is not set
```

The fix seemed obvious — add `load_dotenv()` — but it failed again with the exact same error. The reason: `python-dotenv`'s `load_dotenv()` with no arguments doesn't search from the current working directory by default; it walks up from the *calling file's own location*. The diagnostic script lived in an unrelated scratch directory, nowhere near the project's actual `.env` file, so no amount of `os.chdir()` into the project folder would ever make it find it. The real fix was passing the `.env` path explicitly:

```python
load_dotenv(os.path.join(os.path.dirname(PROJECT_DIR), ".env"))
```

Neither failure was a fact about `agent.py`, `career_watcher.py`, or anything in the real system — both were artifacts of the throwaway script written to *test* those files. The general habit: when a diagnostic fails, check whether the diagnostic itself is well-formed before updating any belief about the system under test. A test harness is code too, and it can be wrong in exactly the same mundane ways (missing setup, wrong assumptions about file resolution) as anything else.

---

## Part 5: Don't cascade an instruction into removing "obviously pointless" dependent code

Near the end of the session, asked to "take the tool out of mcp_server" (since Career Watcher would now run automatically instead of being a conversational tool), the change went further than asked: the whole `MCPServerStdio` connection got removed from `agent.py` too, on the reasoning that a zero-tool `mcp_server.py` made the connection pointless to keep open.

```python
# What got removed (then had to be restored):
async with MCPServerStdio(
    name="main_agent",
    params={"command": sys.executable, "args": [server_path]},
    client_session_timeout_seconds=600,
) as server:
    agent.mcp_servers = [server]
    ...
```

The user's correction: *"I want to keep the mcp_server connection open because I will use it later. I simply wanted the tool out of mcp_server."* The instruction was scoped to one file's contents; the actual change touched two files' worth of architecture. "This has no purpose right now" and "this can be removed" are different claims — the first is observable from the current code, the second requires knowing the user's future plans, which weren't stated and weren't mine to infer. The connection was restored, and only the one tool definition stayed removed.

The general rule: when an instruction's literal scope is narrower than its "obvious" logical consequence, implement the literal scope and name the consequence out loud as a question or a flagged note — don't act on the inference silently, especially in a file the user is actively building toward something with.

---

## Part 6: Verifying a fix means actually running it, not just re-reading the diff

After each code change this session, the change was verified by executing it rather than just re-reading the edited lines:
- After wiring `career_inferences.md` into `get_agent_instructions()`, a standalone script actually called the function and confirmed the real file content (UMKC transcript details, AI-900 cert, business history) appeared in the assembled instructions string — not just that the `if inferences_content.strip():` block was syntactically present.
- After every structural edit to `agent.py` and `mcp_server.py`, `py_compile` was run against both files before considering the edit done.

Neither check is exotic, but both catch a different class of mistake than reading the diff does: `py_compile` catches indentation/syntax breakage from a large restructure (like de-indenting or re-indenting a whole function body), and actually calling `get_agent_instructions()` catches a wiring mistake (wrong path, wrong variable name, condition that never actually triggers) that would look completely correct on a silent read-through.
