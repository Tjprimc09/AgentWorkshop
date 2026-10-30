# Lessons — Job Application Department build session (2026-08-11)

This session covered a lot of ground: fixing `career_watcher.py` twice (once to add MCP, once to remove the agent from it entirely), wiring a real MCP connection into `agent.py`, a long detour into what MCP actually is and how its pieces fit together, and a design conversation about `intake_agent.py`'s weaknesses. Written to be readable cold, six months from now, with no memory of this conversation — so each section explains the situation that prompted it, not just the punchline.

---

## Part 1: MCP, actually explained

### 1.1 The three roles MCP defines, and what each one means

MCP (Model Context Protocol) is a protocol — a standardized way for a program that runs an LLM to talk to a separate program that offers it tools. It was created and open-sourced by Anthropic. Before something like MCP existed, if you wanted an AI agent to use a tool, you wrote custom glue code specific to that exact tool and that exact agent framework. MCP standardizes the conversation between "the thing calling tools" and "the thing offering tools" so that any MCP-speaking caller can talk to any MCP-speaking tool-provider, regardless of who built either side or what language they're written in.

The protocol's own documentation defines exactly three roles, and it's worth having the literal definitions on hand:

- **Host** — the overall LLM application. The thing the user is actually talking to. Examples: Claude Desktop, Claude Code (the tool you're using right now), or in this project, the whole running `agent.py` process.
- **Client** — lives *inside* the host. Manages exactly one connection to one server. If a host wants to talk to three different servers, it holds three separate client objects, one per server. A client's job is narrow: connect, do the handshake, discover what the server offers, forward call requests, relay results back.
- **Server** — a separate process (or remote endpoint) that exposes tools, resources, or prompts. It never talks to the model directly and doesn't know or care who's calling it, as long as they speak MCP correctly.

In this project: `mcp_server.py` is the **server** — it's launched as its own subprocess and, via FastMCP, exposes `search_job_listings` and `check_for_new_career_documents`. `agent.py` is the **host** — the whole interactive program. Inside `agent.py`, the `MCPServerStdio` object is the **client** — it's the thing that actually holds the connection to that one server.

### 1.2 Why "Agent" is not one of these three roles — and how to prove it, not just assert it

A recurring point of confusion this session: is "Agent" (as in, `agents.Agent` from the OpenAI Agents SDK) the same thing as "Client" in MCP's vocabulary? No — and this isn't a matter of opinion, it's checkable directly in this project's own code.

```python
career_watcher_agent = Agent(
    name="Career Watcher",
    model="gpt-5.6-luna",
    instructions="""You are Career Watcher, an expert document analyst. ...""",
)
```
vs.
```python
MCPServerStdio(
    name="career-tools",
    params={"command": "python", "args": [server_path]},
)
```

Only `Agent` has a `model` and `instructions` — the two things that give something the ability to reason about what to do next. Only `Agent` ever gets handed to `ws.run_streamed(agent, prompt)`, the call that actually invokes the LLM. `MCPServerStdio` is never "run" with a prompt anywhere — it's only ever entered as a connection, via `async with MCPServerStdio(...) as server:`, and then *attached to* an `Agent` afterward via `mcp_servers=[server]`. A thing with no model and no instructions has nothing to reason with, and therefore cannot be "the thing that decides which tool to call" — it can only relay a decision something else already made.

This distinction also shows up in how the two are packaged: `Agent` lives in the top-level `agents` module. `MCPServerStdio` lives specifically in the `agents.mcp` submodule — a deliberate separation between "the decision-making thing" and "the MCP connection plumbing," baked into the SDK's own file layout.

### 1.3 `MCPServerStdio` is genuinely the client — proven by reading its actual source, not assumed

Despite having "Server" in its name, `MCPServerStdio` is the **client** half of the connection. The name is confusing on purpose-adjacent grounds: "Server" in the class name refers to what it's connecting *to* (a server), not what the class itself is. This was confirmed by directly reading the installed SDK source rather than trusting the name:

```python
# agents/mcp/server.py, near the top:
from mcp import ClientSession, StdioServerParameters, Tool as MCPTool, stdio_client
```

And inside the class that backs `MCPServerStdio` (`_MCPServerWithClientSession`), the actual connection logic:

```python
self.session: ClientSession | None = None
...
session = ClientSession(...)
server_result = await session.initialize()   # the actual handshake
self.session = session
...
def create_streams(self):
    return stdio_client(self.params)
```

`ClientSession` and `stdio_client` are imported directly from the base `mcp` package — the same official Python SDK for MCP that the tutorial docs use to build their own `Client` convenience class. In other words: the Agents SDK doesn't reinvent MCP's client logic, it just drives the same low-level building blocks itself (`ClientSession`, `stdio_client`) instead of going through the friendlier `mcp.Client` wrapper — because it needs the result in a shape that plugs directly into `Agent(mcp_servers=[...])`, not in the shape the standalone wrapper produces.

### 1.4 What actually happens, mechanically, when a connection opens

Walking through `_run_standalone()` from an earlier version of `career_watcher.py` (the same shape is now used in `agent.py`'s `main()`):

```python
async def _run_standalone():
    server_path = os.path.join(os.path.dirname(__file__), "mcp_server.py")
    async with MCPServerStdio(
        name="career-tools",
        params={"command": "python", "args": [server_path]},
    ) as server:
        await scan_and_update_career_profile(server)
```

1. `MCPServerStdio(...)` is constructed with `params={"command": "python", "args": [server_path]}` — literally the shell command needed to launch the other side: `python mcp_server.py`. Just constructing the object does *not* launch anything yet — it only builds a plain Python object describing how to launch something, later.
2. On `async with ... as server:`, the object actually spawns the subprocess. Now there are two separate running Python processes: the one you're in, and a new child process running `mcp_server.py`.
3. Still inside that `async with` entry, a **handshake** happens over the child's stdin/stdout: the two sides exchange JSON-RPC messages (a lightweight text-based message format used for exactly this kind of "one program asks another program to do something and waits for the answer" exchange) to negotiate capabilities — the client asks "what tools/resources/prompts do you have," and the server answers with each one's name, description, and expected arguments (read from the server's own function signatures/docstrings automatically — nobody hand-writes this schema).
4. The result of all that — a live subprocess handle, plus the discovered tool list — is the `server` object bound by `as server`.
5. That `server` object then gets attached to whichever `Agent` should be able to use it: `agent.mcp_servers = [server]`. Only *now* does the agent actually know these tools exist.
6. When `async with` exits (the block ends), the subprocess gets torn down automatically. This is why everything that might need the connection has to physically be nested *inside* that block — once the block ends, the connection is gone, whether or not you're still holding a reference to the `server` variable.

### 1.5 Trust the primary spec, not confident-sounding secondary sources

Partway through this session, a Gemini-generated answer was brought in as a counter-argument, claiming: *"MCP Agent (The Client): This is the Large Language Model (LLM) or AI application (like Claude Desktop). It decides what needs to be done."* This directly conflated "Agent" and "Client" as synonyms — exactly the opposite of what section 1.2 above establishes.

The tell wasn't the confidence of the writing — it was the citations. Every single footnote on that answer was a Medium post, an SEO blog, or a vendor marketing page — not `modelcontextprotocol.io`, the actual spec published by the protocol's own authors. And there was a second tell inside the answer's own content: its "Examples" row for "MCP Agent (Client)" listed *entire applications* — "Claude Desktop, Cursor IDE, LangChain." That's not describing a narrow connector object like `MCPServerStdio`; it's describing the **Host** role (the whole app), informally mashed together with "Client" for a beginner audience. That's a real, common simplification for people who just want to *use* an MCP-enabled product — it's just not the same thing as the finer-grained roles the actual SDK code implements, where Host, Client, and the model-driven reasoning are three separate pieces, not one blob.

The general lesson: a plausible-sounding synthesis of popular content and the literal wording of a primary spec can both exist and disagree, and an LLM asked to explain a concept will often reproduce whatever's most common in its training data — which, for a fast-moving spec like MCP, is often *simplified blog content*, not the spec itself. When a technical claim is disputed, check what's actually being cited before trusting either side, your own included.

---

## Part 2: The async-timing constraint, and the bug pattern it creates

### 2.1 Why an agent can't be born already holding a live connection

`career_watcher_agent = Agent(...)` (when it still existed) sat at the top level of `career_watcher.py` — meaning it runs the instant the file is loaded by Python, before any of the file's actual logic starts executing. This is true of *any* plain assignment at the top level of a `.py` file: it happens immediately, synchronously, top to bottom, the moment the file is imported or run.

Opening an MCP connection, by contrast, requires *async* work: spawning a subprocess and then waiting for a back-and-forth handshake to finish, without freezing the rest of the program while waiting. Python can only do that kind of "pause and wait for something else, without locking up" work once it's running inside something that has actually started an event loop — the underlying machinery, kicked off in this project by the line `asyncio.run(_run_standalone())` (or, in `agent.py`, `asyncio.run(main())`) at the very bottom of the file. Until that line runs, Python is just executing plain top-to-bottom code with no way to pause-and-wait for anything.

So there's a hard ordering fact here, not a preference: by the time a module-level line like `career_watcher_agent = Agent(...)` executes, it is *structurally impossible* for a live MCP connection to already exist, because nothing has started the async machinery yet. This is exactly why the original, broken version of this code —

```python
career_watcher_agent = Agent(
    name="Career Watcher",
    ...
    mcp_servers=[mcp_server],   # <- `mcp_server` was never defined anywhere in the file
)
```

— could never have worked. Whoever wrote it needed a connection that plain, synchronous, module-load-time code cannot produce.

### 2.2 The actual fix: build the agent with nothing, attach the connection later

The correct sequence, worked out and applied twice this session (once in `career_watcher.py`, before that file's agent was removed entirely; once in `agent.py`, which still uses this pattern):

1. Build the `Agent` at module level with no `mcp_servers` argument at all — it simply doesn't have that capability yet.
2. Later, inside an actual `async def` function that's running after `asyncio.run(...)` has started things up, open the real connection with `async with MCPServerStdio(...) as server:`.
3. Attach it to the already-built agent: `agent.mcp_servers = [server]`.

```python
# agent.py, current shape
agent = Agent(
    name="Job Getter",
    model="gpt-5.6-luna",
    instructions=get_agent_instructions(),
    )   # <- no mcp_servers here; can't exist yet

async def main():
    server_path = os.path.join(os.path.dirname(__file__), "mcp_server.py")
    async with MCPServerStdio(
        name="main_agent",
        params={"command": "python", "args": [server_path]},
    ) as server:
        agent.mcp_servers = [server]   # <- attached here, once the connection is real
        # ...everything that needs this connection goes inside this block...
```

### 2.3 The bug this pattern invites, and why it's not just a hypothetical

Splitting "build the object" from "give it its real configuration" across two distant points in a file creates a gap where a broken or forgotten piece can sit unnoticed — and this happened twice in this exact session, in two different files:

- In `career_watcher.py`: a half-finished edit left `mcp_servers=[mcp_server]` sitting in the `Agent(...)` constructor, referencing a name (`mcp_server`) that didn't exist anywhere in the file. The file couldn't even be imported without crashing, and this went unnoticed until it was specifically read through line by line.
- In `agent.py`: the exact same shape of bug — `tools = [save_candidate_fact]` followed by `mcp_servers = ` with nothing after the equals sign — a syntax error, plus a reference to `save_candidate_fact` that (at the time) wasn't even imported from anywhere that existed (`tools.py` didn't exist yet).

The deeper lesson isn't just "these two specific bugs happened" — it's that *this shape of code organization is what let them hide*. When you can't tell what an object's real, final configuration is without jumping to a completely different part of the file (or a different file), a broken or missing piece doesn't jump out the way it would if everything relevant were in one place. Where the async constraint doesn't actually force a split — a plain native tool needs no handshake at all — there's no reason to still split things apart out of habit. `career_watcher_agent`'s tool (when it still had one, before the whole agent was removed) was attached directly in the constructor: `Agent(..., tools=[save_candidate_fact])`, no deferred second step, because nothing about a native tool requires one.

---

## Part 3: Native tools vs. MCP — a concrete decision rule, not just a tradeoff table

### 3.1 The actual deciding question

Lessons2 already covered the abstract tradeoff (MCP: standard protocol, discoverable, reachable by any compliant client, at the cost of a separate process and a handshake; `@function_tool`: simpler, but locked to one Python process and one SDK). This session turned that into a concrete, checkable question: **does this specific consumer already need an MCP connection for some other reason?**

- `career_watcher_agent` (before it was removed) had no other reason to talk to MCP at all. Giving it `save_candidate_fact` via MCP would have meant spinning up an entire extra subprocess and handshake for exactly one tool call, with no other benefit — so it got the tool as a plain `@function_tool` instead, no MCP involved.
- `agent.py`'s Job Getter, by contrast, *already* needs an MCP connection to `mcp_server.py` for `search_job_listings`. Given that connection has to exist anyway, adding `check_for_new_career_documents` to the same `mcp_server.py` file means Job Getter reuses a connection it already needs — instead of ending up with two separate, parallel ways of getting tools (`tools=[...]` for one thing, `mcp_servers=[...]` for another), which would be more moving parts for no real benefit.

### 3.2 What this looked like as actual code changes

`mcp_server.py`, before and after `save_candidate_fact` moved out:

```python
# Before — save_candidate_fact lived here, exposed via MCP, even though
# career_watcher_agent (its only real caller at the time) didn't need MCP for anything else.
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("career-tools")

@mcp.tool()
def search_job_listings(query: str):
    return "search results"

@mcp.tool()
def save_candidate_fact(category: str, fact: str) -> str:
    ...
```

```python
# After — save_candidate_fact moved to tools.py as a plain @function_tool.
# mcp_server.py now only holds things that genuinely benefit from being MCP tools.
from mcp.server.fastmcp import FastMCP
from career_watcher import scan_and_update_career_profile
mcp = FastMCP("career-tools")

@mcp.tool()
def search_job_listings(query: str):
    return "search results"

@mcp.tool()
def check_for_new_career_documents() -> str:
    return scan_and_update_career_profile()
```

```python
# tools.py (new file)
from agents import function_tool

@function_tool
def save_candidate_fact(category: str, fact: str) -> str:
    ...
```

Note also: both MCP tools ended up as plain (non-`async`) functions. `check_for_new_career_documents` originally awaited `scan_and_update_career_profile()`, but once that function itself stopped doing any async work (see Part 4), there was nothing left to `await` — so the wrapper became sync too, matching `search_job_listings`'s existing style rather than staying `async` out of habit.

---

## Part 4: The full story of `career_watcher_agent` — from built, to tooled, to deleted

This arc is worth telling in full because each step changed the reasoning, and the final answer would look arbitrary without the steps that led there.

### 4.1 Stage one: it was an `Agent` with an attached-but-unused tool

At the point this session picked the file up, `career_watcher_agent` looked like this:

```python
career_watcher_agent = Agent(
    name="Career Watcher",
    model="gpt-5.6-luna",
    instructions="""You are Career Watcher, an expert document analyst.
Your job is to read text extracted from resumes, cover letters, transcripts, or certification documents,
and extract key facts about the candidate: ...
Format your output as clear Markdown bullet points categorized by category.
Only include factual details found directly in the text.
""",
    tools=[save_candidate_fact],
)
```

Two things were true and worth separating: it genuinely called an LLM (extracting structured facts from freeform, differently-formatted resume text is real semantic judgment — a hand-written parser trying to do the same job reliably across every resume format anyone might upload would be enormous and brittle, a point confirmed by reasoning through it directly rather than just assumed). But the `tools=[save_candidate_fact]` attached to it was dead weight: nothing in the instructions ever told the model to call that tool, and even if it somehow decided to anyway, `save_candidate_fact` writes to `Career/profile.md` — a completely different file than where this agent's actual output goes (`Career/facts.md`, written by plain Python code around the agent, not by the agent itself). A tool being *available* and a tool being *used* are different facts, and only checking the instructions text (not just the `tools=` list) tells you which one is true.

### 4.2 Stage two: does it need the `Agent` wrapper specifically?

A more precise question than "does this need an LLM": does it need the SDK's `Agent` *class* — which sets up a tool-calling loop, letting the model decide to call something, see the result, and keep going — or would a single, one-shot call to the model do the same job with less machinery? `career_watcher_agent` was called once per document: text in, markdown text out, no back-and-forth, no tool ever actually invoked. That's a one-shot transformation, and the `Agent` wrapper's main value (the loop) was sitting unused. This is a genuinely separate axis from "does it need an LLM at all" — a raw, direct call to the model API and a full `Agent` object both use the same underlying model judgment; the only difference is whether there's decision-loop machinery wrapped around it that never actually gets exercised.

### 4.3 Stage three: could it be improved instead — early inferences alongside the literal facts?

Before landing on removal, the design briefly went the other direction: have `career_watcher_agent` note not just literal facts, but small, hedged, per-document inferences too ("Early Observations"), so a later consolidation stage would have more to work with. The instructions draft that was written for this:

```
After the facts, add a section titled "**Early Observations:**" with any preliminary
judgments or inferences this document suggests but doesn't state outright — for example,
implied skills, likely seniority level, or possible career direction. Keep these clearly
separate from the facts above, hedge every statement ("may indicate," "could suggest"),
and never invent specifics (names, dates, numbers) that aren't grounded in the text.
```

The concern this raised and then resolved: wouldn't this duplicate `intake_agent.py`'s job (which does cross-document consolidation and inference)? Resolution: no, because these would be *cheap, per-document* hunches captured while the model already has the text in front of it — not a full cross-document reconciliation, which only `intake_agent.py` can do since it's the only stage that ever sees *all* the documents together. Different kind of judgment, different scope, not actually redundant.

### 4.4 Stage four: the actual final answer — no agent at all, semantic judgment moves entirely to intake_agent.py

This got reversed by an explicit decision: `career_watcher_agent` shouldn't exist at all, and none of stage 4.3's design should be implemented. The reasoning, once stated: `intake_agent.py` was already the intended place to do consolidation and inference — collapsing the "categorize this document's facts" judgment call into that same later stage (instead of splitting it across two places) is simpler, and `career_watcher.py`'s job shrinks to something purely mechanical: find new/changed files, pull their raw text out, write that raw text into `facts.md`, done. No structuring, no summarizing, no early observations — just capture.

This is the resolution of the "needs an LLM" vs. "needs an LLM *here*" distinction: the *task* (turning resume text into structured, judged facts) still absolutely needs an LLM — nothing about that changed. What changed is *where in the pipeline* that judgment happens. It doesn't have to happen at the exact place the raw text first gets touched; it can be deferred entirely to whichever stage is best positioned to do the full job.

### 4.5 What the removal actually changed in the code

```python
# Before: async, called the model, appended the model's structured output
async def scan_and_update_career_profile():
    ...
    async with responses_websocket_session() as ws:
        for members in groups.values():
            ...
            text = extract_file_text(full_path)
            if text:
                prompt = f"Analyze this document ({rel_path}) and extract candidate background facts:\n\n{text}"
                result = ws.run_streamed(career_watcher_agent, prompt)
                async for event in result.stream_events():
                    pass
                extracted_output = result.final_output
                if extracted_output:
                    new_facts.append(f"### Source: {rel_path}\n{extracted_output}\n")
```

```python
# After: plain def, no model call, raw extracted text goes straight in
def scan_and_update_career_profile():
    ...
    for members in groups.values():
        ...
        text = extract_file_text(full_path)
        if text:
            new_facts.append(f"### Source: {rel_path}\n{text}\n")
```

Along with the function itself dropping `async`, its entry point simplified from `asyncio.run(scan_and_update_career_profile())` to a direct call — there's nothing async left inside it to wait for, so wrapping it in `asyncio.run` would have been machinery with nothing to do.

### 4.6 The downstream consequence this created, and why it's probably fine but genuinely unverified

`intake_agent.py`'s `generate_inferences()` reads all of `facts.md` and hands it, as one text blob, to a five-agent stakeholder panel as "here are the candidate's facts." Before this session, that blob would have been LLM-categorized bullet points (skills / experience / education / certifications, each clearly labeled). After this session's change, it's raw, unstructured document text instead — whatever the original resume or cover letter literally said, unprocessed. This is very likely still fine, since LLMs are generally good at reading raw prose, but it's a different input shape than whatever `intake_agent.py` was actually tested against when it was built and verified working (a small sample fact, not a real `facts.md`) — so it's flagged as an open, untested question, not a known-good fact.

---

## Part 5: Tracing the real dependency graph instead of guessing

When deciding where a new piece of glue code should live — specifically, the wrapper that lets `mcp_server.py` call `scan_and_update_career_profile()` — the deciding factor was the *actual* import chain in the project, not intuition:

```
agent.py  →  career_watcher.py  →  tools.py
```

This is one-directional: `agent.py` imports from both of the other two files, but neither of them imports from `agent.py`. That means `agent.py` can safely import anything from either file with zero risk of a circular import. If the new wrapper had instead been placed in `tools.py`, it would have needed `tools.py` to import *from* `career_watcher.py` — but `career_watcher.py` already imports `save_candidate_fact` *from* `tools.py`, so that would have created a genuine circular import (`tools.py → career_watcher.py → tools.py`), fixable only with a deferred/inside-the-function import trick. (In the end, the wrapper actually landed in `mcp_server.py` itself, once the decision in Part 3 was made — but the same graph-tracing method applies: `mcp_server.py` isn't imported by either other file, so it was equally safe.) The general habit worth keeping: when a "where should this code live" question turns on whether an import would be circular, actually write out the current import chain rather than guessing from a gut sense of which file "feels" more related.

---

## Part 6: A tool's return value has to serve whoever actually calls it

`scan_and_update_career_profile()` originally returned `None` on every single code path and only ever communicated status via `print()` — completely fine when the only consumer was a person watching a terminal run the script directly. Once it needed to become something a *conversational agent* calls and then has to summarize back to a human, "what happened" needed to become a real return value on every branch, because there was no other way for that information to reach anyone:

```python
if new_facts:
    try:
        with open(FACTS_FILE, "a", encoding="utf-8") as f:
            ...
        message = f"[Career Watcher] facts.md updated with {len(new_facts)} document(s): {', '.join(processed_names)}."
    except Exception as e:
        message = f"Error updating facts.md: {e}"
else:
    message = "[Career Watcher] No new or updated career documents found."   # <- this branch used to print nothing at all

print(message)
save_processed_index(index)
return message
```

The "nothing new found" branch is worth calling out specifically — before this change, that case printed *nothing whatsoever*, meaning a caller (or a person) had no way to distinguish "it ran and found nothing" from "it silently failed to run." Every outcome a caller might care about needs its own explicit signal, not just the outcomes that happen to be the interesting ones.

---

## Part 7: Comments go stale exactly like code does

While scaffolding `agent.py`'s `main()` for a "fill in the connection code yourself" exercise, a comment was written that said:

```
# 2. Attach that live connection to `agent`, so it can call MCP tools
#    (like save_candidate_fact) at any point during the conversation.
```

A few edits later, `save_candidate_fact` moved out of `mcp_server.py` into `tools.py` as a plain native tool — meaning it was no longer reachable through this MCP connection at all. The comment, however, didn't get updated automatically just because the code it referenced changed; it just sat there, now describing something untrue. When this got flagged, the actual dependency was double-checked (nothing else in `agent.py` referenced `save_candidate_fact`) — it turned out to be a stale example inside a comment, not a real leftover coupling. The lesson: comments that name specific functions or files as examples are claims about the codebase at the moment they were written, and they don't automatically stay true — they need the same "is this still accurate" check that code does, especially right before trusting one as evidence of how something currently works.

---

## Part 8: Automatic triggering on Windows — what "simple" actually costs

### 8.1 The real fork isn't "automatic vs. not," it's "hidden process vs. no process"

The initial ask was for `career_watcher` to run automatically whenever a file got added to `Career/` — "nothing rough or laborsome," and specifically triggered by a file being *created*, not moved. Genuine real-time (non-polling) filesystem watching on Windows requires the operating system to notify *something* the moment a file changes — and that "something" has to already be running to receive the notification. There is no way around this: real-time detection needs a live listener.

That leaves exactly two shapes such a listener can take, and they trade off very differently:

- **A small script, using the `watchdog` library, listening specifically for "created" events** (not "modified," not "moved" — `watchdog` distinguishes these, and the ask was specifically to exclude moves) — auto-started by Windows Task Scheduler with an "at log on" trigger, running invisibly in the background the whole time you're logged in, the same way a sync client like OneDrive or Dropbox does. You never manually start it; Windows does, every time you log in.
- **A genuinely process-free approach**, where Windows' own built-in security-auditing feature detects the file-creation event and writes it to the Event Log, and Task Scheduler is configured to react to that specific log entry by launching `career_watcher.py` once. Nothing is "running" in between calls at all. This is real, but it is exactly the "rough and laborsome" setup that was explicitly being avoided: it requires enabling object-access auditing, setting a SACL (a security permission entry) on the folder specifically for auditing purposes, and correlating the right Event ID in Task Scheduler — meaningfully more setup than the first option for the same end result.

### 8.2 Why the first option still got rejected, and what that revealed

Even the "hidden, auto-started, you never touch it" option was rejected — not because it doesn't work, but because of a stated concern: *"I will eventually close the fucking program... if I want this to continuously run, I have to keep it open."* This surfaced a mismatch between what "runs in the background" technically means and what it can feel like from the outside — a background process is still a process, and the worry was about relying on *something* being alive at the moment a file gets dropped, at all, ever, regardless of how invisibly it starts. That's a legitimate, different constraint than "don't make me manually launch a terminal," and it ruled out both Windows-automation options, not just the more complex one.

### 8.3 The actual final answer: no automation at all — invoke it deliberately, in conversation

The eventual resolution moved away from any form of automatic triggering entirely: `career_watcher`'s scan becomes something Job Getter can call **on demand**, only when the user explicitly asks for it in conversation (e.g. "check for new resumes I added"), via the MCP tool `check_for_new_career_documents`. Nothing runs unless a live conversation with Job Getter is happening and the user asks for it. This sidesteps the whole "does something need to always be running" question by simply not needing anything to always be running — the cost is that it only works while you're actively talking to Job Getter, not truly passively.

---

## Part 9: "Invoke it like a skill" needs disambiguating — it can mean two different systems

When the idea of triggering `career_watcher` "like a skill" came up, it turned out to have two genuinely different possible meanings, each implying different code:

- **A literal typed command, intercepted by code before it ever reaches the model** — the same pattern `agent.py`'s chat loop already uses for `quit`/`exit`:
  ```python
  if user_input.lower().strip() in ["quit", "exit"]:
      break
  ```
  If `scan_career` (or similar) worked this way, typing it would be caught directly by an `if` check and would call `scan_and_update_career_profile()` straight away — the model never even sees that input, and there's no chance of it failing to recognize intent, because there's no judgment involved at all; it's a plain string match.

- **A natural-language request that the model recognizes and decides to act on** — you say something like "check for new resumes I added," Job Getter's underlying model reads that, recognizes it matches `check_for_new_career_documents`'s docstring, and calls the tool itself. This is what actually got built, via MCP. It's more flexible (any phrasing that conveys the right intent works, not just one exact string) but depends on the model correctly reading intent every time — a reliability tradeoff a literal command doesn't have.

These aren't cosmetically different — they're different mechanisms with different failure modes, and "invoke it like a skill" was genuinely ambiguous between them until directly asked. Worth remembering as a general pattern: "trigger X when I say Y" always has this fork hiding inside it (exact match vs. model-judged intent), and it's worth naming explicitly rather than assuming which one is meant.

---

## Part 10: Groupthink in a multi-agent panel is a structural problem, not a wording problem

`intake_agent.py`'s five-stakeholder panel (Recruiter, Hiring Manager, Technical Lead, HR, Executive) runs two rounds: an initial read from each, then a reaction round where each is told to "agree, disagree, or add something the others missed." The gap identified: that instruction *permits* disagreement but creates no actual pressure to find it. LLMs default toward being agreeable without something actively pushing against that tendency, so a nominally-open option like "or disagree" tends to go unused in practice — the panel is more likely to converge into polite consensus than to actually surface real tension.

The fix direction, framed around a jury analogy (a jury's structure forces individual justification, not just group agreement): require each stakeholder to actively produce something adversarial before a round counts as complete — name a specific reason they might be wrong to agree with the group, or a specific risk/gap the others are missing — rather than leaving "disagree" as an easy-to-skip checkbox alongside "agree." The general principle: if you want a multi-agent system to produce real debate instead of consensus theater, the instructions need to *require* friction, not just *allow* it.

---

## Part 11: What a real eval requires — three concrete pieces, not a vibe check

Distilled from a conversation about `intake_agent.py`'s lack of any evaluation at all: knowing whether a change to a prompt or a panel's instructions actually made output *better* requires three specific things to exist, not just careful reading of one run's output:

1. **A small set of example inputs paired with known-good expected outputs** — for this project, that would mean some sample `facts.md` content with inferences a human has already judged to be good, sitting alongside it as a reference.
2. **A rubric** — an explicit description of what "good" means for this task: is the inference actually grounded in the given facts? Is it hedged appropriately, not stated as certain? Would a human reviewer looking at the same facts plausibly agree with it?
3. **A repeatable way to score new output against that rubric** — run the same examples through the current version of the system, score the result against the rubric (by a human, or by a separate, narrowly-scoped LLM judge — see Lessons2's eval-flavors section on why a *separate* judging pass beats self-critique), and get a number or a pass/fail you can compare across versions.

Without all three existing together, "did this change help" collapses into eyeballing a single run and trusting your own impression of whether it reads better — which is exactly the kind of judgment-without-verification this project has otherwise been trying to avoid.

---

## Part 12: Files in this project get hand-edited between turns — always re-read before touching anything

Several times this session, a file's actual on-disk content had already diverged from whatever was last read earlier in the conversation — not because of any tool used here, but because the file was open in an editor and being edited directly, in parallel with this conversation. Concrete instances: the `Agent` block in `career_watcher.py` was fully deleted by hand between one turn and the next, with no announcement; a half-finished, syntactically broken attempt at wiring `mcp_servers` showed up in `agent.py`; and later, an in-progress fix to that same code appeared with correct indentation on some lines but not others.

Each time, re-reading the actual file immediately before making any edit (rather than trusting a mental model built from earlier in the conversation) caught the divergence before it caused a problem — an edit based on stale assumptions would have either failed outright (trying to match text that was no longer there) or, worse, silently overwritten in-progress work. The practical rule this produces: in a project like this one, where the person you're working with edits files directly outside of what's visible in the conversation, never assume a file's state — read it fresh, immediately before editing it, every time.
