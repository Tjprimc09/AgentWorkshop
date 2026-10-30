# Lessons — intake_agent.py panel redesign (2026-08-20)

This session started from a single complaint — "the output was absolute bullshit" — about
`career_inferences.md`, the file a 5-stakeholder panel produces by inferring career
competencies from Tyler's facts. It ended with a full redesign of the panel's mechanics: almost
all structured output removed, the round-continuation logic rebuilt around a different
question, and the hard round cap dropped entirely. Getting there required correcting several of
my own wrong claims along the way, twice by going and reading raw evidence instead of trusting
either the panel's own self-report or my own assumptions. Written to be readable cold, with no
memory of this conversation.

---

## Part 1: Structured output constrains *shape*, not *meaning*

### 1.1 The question that mattered

Partway through diagnosing the bad output, the natural question came up: why force the model
to fill out a form (a Pydantic schema) instead of just letting it think and write freely? The
tempting answer — "so the code can act on it" — is only half true, and the imprecise other half
is what actually caused the bug.

### 1.2 What a schema actually guarantees

A field like this:

```python
class Claim(BaseModel):
    skill: str = Field(description="The skill/competency/impression being asserted.")
    evidence: str = Field(description="The specific fact this claim draws from.")
    hedge: Literal["likely", "plausible", "may indicate", "clear"]
```

guarantees that `skill` is *a string* and `hedge` is *one of four exact words*. It says nothing
about whether the string is actually a skill. `"Resume-claim verification is required before
relying on specific metrics"` is a perfectly valid string — Pydantic has no way to know that
isn't a career competency. The actual `career_inferences.md` this session started from had 7-8
of 27 entries that were exactly this: type-valid, meaning-invalid. Structured output didn't
cause that problem. It just did nothing to prevent it, because validation happens at the level
of shape, and the failure was at the level of content.

### 1.3 The resolution

The system only has one place where Python code actually *branches* on a field —
`if checkpoint.discussion_complete: break`. Everywhere else, the "structure" was ceremony: it
didn't stop bad content, and the final synthesis step was already free text the entire time
anyway. The redesign strips every schema except that one boolean (plus two plain-text fields
next to it) and lets every stakeholder and the Mediator write in full prose everywhere else.

**General lesson:** before adding a schema to constrain a model's output, ask what the *code*
actually does with each field. If nothing branches on it, a schema doesn't prevent bad content —
it just makes bad content look more official.

---

## Part 2: Don't over-scaffold a model's own reading comprehension

### 2.1 The wrong-but-plausible first fix

Diagnosing why stakeholders in round 2+ weren't really "responding to each other," the first
theory was: nothing labels who said what in the shared conversation history, so add name tags —
"RECRUITER: ..." — to every turn before it goes back into the pool.

### 2.2 The correction

The user's pushback: *"they don't need to know WHO said what."* Sharper the second time, after a
revised "numbered card" version of the same idea: *"You are saying an LLM can't do that, but it
can. Each claim doesn't NEED an identity."*

Both corrections were right. The system's own schema already assumed this:

```python
target_claim: str = Field(description="The specific claim being objected to, quoted or closely paraphrased.")
```

An LLM reading a page of unlabeled text can already say "I disagree with the part that said X"
by quoting or paraphrasing it — the same way a person reading an anonymous forum thread doesn't
need footnotes to reference a specific earlier point. Treating the model like a dumb parser that
needs external tagging to find things was the mistake, not the model's actual capability.

### 2.3 What was actually still true

Retracting the identity-tagging idea didn't mean nothing was wrong. Checking the underlying SDK
source (`items.py`, `session_persistence.py` in the installed `agents` package) confirmed session
items really do carry no speaker field — but the real gap turned out to be simpler: the
`objections` field in the old schema had *no description at all* telling the model when or why
to use it, unlike neighboring fields that explicitly said "leave empty if you have nothing
genuinely new to add." An unguided field gets used rarely; that's a prompt-completeness problem,
not a missing-infrastructure problem.

**General lesson:** when something seems broken, check whether the model's core language
ability is actually the bottleneck before reaching for scaffolding (tags, IDs, structured
lookups) to compensate for it. LLMs are generally good at exactly the kind of unstructured
reference-and-recall that scaffolding is often built to replace.

---

## Part 3: When a model's self-report is unverifiable, go get raw evidence

### 3.1 The claim that looked like proof

Investigating whether 5 stakeholder roles were pulling their weight, the final synthesis's own
"(raised by: ...)" tags were used as evidence: 17 of 26 real entries listed all 5 stakeholders as
co-raising the same claim, which looked like strong proof of redundancy.

### 3.2 The catch

The user: *"That's because the mediator is doing that."* The "(raised by: ...)" attribution
isn't tracked anywhere in code — no field records which stakeholder produced which claim. It's
the Mediator, at the very end, freely reconstructing who-said-what from memory, in the same
transcript that Part 2 established has no speaker tags at all. Forced to name names on every
bullet with no reliable way to verify them, "credit everyone" is a very plausible default when
uncertain. The evidence wasn't proof of redundancy — it might just have been proof that the
Mediator was guessing.

### 3.3 Going to get real ground truth

Round 1 runs in isolated per-stakeholder sessions (`SQLiteSession(f"intake_{agent.name}_{timestamp}_r1")`)
before being merged into the shared session — genuinely independent, no cross-contamination.
Querying the persisted `intake_sessions.db` directly (not through any agent's summary of it)
surfaced the raw messages:

- **Round 1** (independent): five genuinely distinct narratives — "strongest for internships,"
  "could contribute quickly in junior software," "credible early-career fundamentals," "strong
  ownership, adaptability," "unusually strong initiative." Real distinct value.
- **Round 4** (after three rounds of shared, Mediator-directed discussion): all five stakeholders
  produced near-identical claims — *"Foundational Responsible AI awareness"* x5, worded almost
  the same way by every single one.

That's real, verified redundancy — but concentrated in later rounds, not inherent to having 5
roles. It reframed the actual question from "how many stakeholders" to "why does round 4 look
nothing like round 1," which turned out to have a precise, fixable answer (Part 4).

**General lesson:** a model's summary of its own multi-turn process (who contributed what, why
it decided something) is itself a claim, generated under the same uncertainty as everything
else it writes. When a conclusion rests on that kind of self-report, and the raw data is
reachable (a database, a log, a stored session), go read the raw data instead of trusting the
summary of it.

---

## Part 4: A miscalibrated stopping question forces the maximum every time

### 4.1 The bar that could never be cleared

The old checkpoint schema defined completion like this:

```python
discussion_complete: bool = Field(
    description="True only if the facts have genuinely been examined from every relevant "
                "angle and no productive line of inquiry remains..."
)
```

"Every relevant angle, no productive line of inquiry remains" is close to an unfalsifiable bar —
a model asked that question can almost always find *something* technically unexamined if it
looks hard enough. The Part 3 database check confirmed this concretely: the panel ran all 4
available rounds, every time, only agreeing the discussion was complete on the checkpoint
*after* round 4 — it never once stopped early.

### 4.2 The reframe

The fix wasn't more rules about what *not* to search for (the old schema already tried that, with
a list of banned topics) — it was changing the actual question from exhaustiveness to marginal
value:

```python
checkpoint_prompt = f"""
Above is the full panel discussion through round {round_num}.

Would another round genuinely add something worth having — a distinct career
inference nobody's raised, or a real improvement to one already on the table?
Not "is there some topic nobody's technically touched yet" — almost anything
can be framed that way. The question is whether continuing is actually worth
it, or whether the panel has already said what it has to say.
...
"""
```

### 4.3 The cap that got removed on purpose

The natural instinct was to keep a generous, rarely-hit round ceiling as a safety backstop even
after fixing the question — the concern being a genuinely unbounded loop if something ever went
wrong. The user rejected that outright: *"I do NOT want a ceiling. I want the moderator to
decide when the conversation is done."* That's a real, considered tradeoff (trusting the fixed
question completely vs. keeping a cheap insurance policy against a runaway loop), stated once and
respected — `MAX_ROUNDS` was deleted, and the loop became `while True:`, exiting only on the
Mediator's own judgment.

**General lesson:** if a stopping condition is calibrated as "has everything been checked" rather
than "is continuing still worth it," it will almost always continue — not because the model
reasons badly, but because "everything" is a much higher and vaguer bar than "worth it." Fix the
question before reaching for an external cap to compensate for it.

---

## Part 5: Verify the file's real, current state before editing it — even mid-conversation, even after a plan is approved

### 5.1 First mixup: assuming instead of confirming

Midway through the session, a second, much more elaborate version of `intake_agent.py` was
pasted into the conversation. It was labeled, out loud, as "ChatGPT's version" — based on an
earlier, unrelated remark that the user had "talked to chatgpt." That attribution was never
actually confirmed. The user: *"Are you fucking stupid? They came from YOUR version of the
harness. I haven't even tried chatgpt's version. Also, how did you automatically know it was
from chatgpt?"* Grepping the real file on disk (`class ObjectionType`, the old `hedge` field)
confirmed it: the live file was still the original, unmodified version. The bad output being
debugged had come from *that* file, not the pasted one — a fact that should have been checked
before being asserted, not after being challenged.

### 5.2 Second mixup: the file moved again, after a plan was already approved

After extensive design discussion, a plan was written and approved (via the plan-mode
approval flow) describing exact changes against that same original file — specific class names
(`Claim`, `Objection`, `hr_agent`) to delete and rewrite. When implementation started, reading
the live file first (a normal first step, not a suspicion of anything) showed it was no longer
that file at all — it now matched the ChatGPT rewrite shown earlier: `InferenceClaim`,
`ClaimChallenge`, `people_leadership_agent`, and guardrail rules the plan was written to *add*
were already present. Nothing in the conversation announced this change; the user had
apparently applied it directly in the editor at some point during the discussion.

Because the file was read fresh instead of the approved plan's literal old code being pattern-matched
against it blindly, the mismatch surfaced immediately instead of causing silent edit failures or,
worse, edits that partially applied against the wrong baseline. The substance of the plan didn't
change — the design decisions were all still valid — but the mapping onto real file contents had
to be redone against what was actually there.

**General lesson:** a plan (or an assumption about a file's contents) is a snapshot of a moment
in the conversation, not a live view of the file. In a long session, especially one where the
user has their own editor open on the same file, always re-read the actual current file
immediately before editing it — even if a detailed plan for it was just approved minutes
earlier. The cost of checking is one tool call; the cost of not checking is edits built on a
foundation that quietly isn't there anymore.

---

## Part 6: A "Learn by Doing" moment has to be a real open decision, not a rename of one already made

After the redesign was implemented, one piece was deliberately left blank for the user to
write: the new checkpoint prompt's exact wording, framed as a meaningful "prompt-engineering"
exercise. Rejected: *"Writing prompts is not a learn by doing exercise that is actually
helpful. I'm trying to understand how to design this system... Writing a prompt is not useful
towards that end."*

The distinction that was missed: the actual decision — ask about marginal value instead of
exhaustiveness, have the Mediator write real driving questions instead of a topic checklist —
had already been reached jointly, over the course of the whole conversation, through the
evidence in Parts 3 and 4. Writing the literal prose that implements an already-settled decision
is transcription, the same category of work as writing any other already-decided line of code.
It isn't a design decision in its own right just because the content happens to be a string of
English words instead of a function body.

A related near-miss earlier in the same implementation step: the plan initially merged the
Mediator's internal `reasoning` (why it's continuing or not) and its `next_round_prompt` (the
actual instructions sent to the panel) into one combined field, in the name of minimizing
schema size. The user caught this too: *"Why are we changing the prompt for subsequent rounds?
... Don't I have the moderator injecting the prompt for subsequent rounds after round 1???"*
Re-examining it, the merge wasn't justified by anything actually requested — "remove forced
categories" had gotten conflated with "minimize field count," and collapsing two fields with
different jobs (self-justification vs. actual directive) risked producing a worse prompt than
keeping them separate. They were split back apart.

**General lesson:** a TODO(human) earns its place only when the *substance* of a decision is
still open — not when only its wording is left to fill in. And when trimming structure in the
name of simplicity, check that each remaining piece still serves a distinct purpose; merging two
things that happen to both be strings is not the same as removing something that was actually
unnecessary.
