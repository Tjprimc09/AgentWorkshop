import os
from datetime import datetime

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from agents import Agent, responses_websocket_session, SQLiteSession
from panel_consolidator import panel_consolidator
from utils import stream_and_print

load_dotenv()

FACTS_FILE = os.path.join(os.path.dirname(__file__), "facts.md")
INFERENCES_FILE = os.path.join(os.path.dirname(__file__), "career_inferences.md")
INTAKE_SESSIONS_DB = os.path.join(os.path.dirname(__file__), "intake_sessions.db")


# ---------------------------------------------------------------------------
# Shared panel objective
# ---------------------------------------------------------------------------

COMMON_GUARDRAILS = """
Your shared mission is to help build the richest defensible catalogue of
career-relevant knowledge that can be inferred from the candidate's facts.

The catalogue should capture what the candidate's history suggests about
their skills, competencies, capabilities, working patterns, professional
strengths, technical understanding, leadership, business ability, and other
useful career signals.

Rules:

1. Ground every inference in the supplied facts. Never invent numbers, dates,
   employers, accomplishments, responsibilities, technologies, or experience.

2. Reason beyond literal restatement when the evidence supports it. The purpose
   of this panel is inference. Multiple facts may combine to support a
   meaningful conclusion that no single fact states explicitly.

3. Look for connections across the candidate's history. Career value often
   emerges from combinations of technical, business, academic, operational,
   leadership, and project evidence.

4. Decompose broad umbrella claims when the evidence supports distinct useful
   competencies. For example, business ownership may support separate
   inferences about client management, estimating, project planning, resource
   coordination, risk management, quality control, negotiation, or ownership.

5. Prefer specific, useful claims over vague labels. "Technical ability" is
   weak. "Likely able to translate ambiguous business requirements into
   implementable software scope" is useful when supported.

6. Distinguish confidence from usefulness. A useful inference may have only
   moderate confidence. Express uncertainty through the confidence level and
   boundary rather than discarding the inference.

7. Do not create standalone catalogue entries whose primary meaning is that
   something is missing, unverified, unproven, inconsistent, unavailable, or
   requires clarification.

   Examples that should not become standalone career inferences:
   - "Production ML readiness is unverified."
   - "Collaborative engineering ability remains unproven."
   - "Resume claims require verification."
   - "Availability requires clarification."

   If missing evidence materially limits an otherwise useful inference, record
   that limitation in the claim's boundary.

8. Absence of evidence is not itself career knowledge. Do not search for
   missing competencies merely because they have not been discussed.

9. Challenges and skepticism exist to improve the catalogue. Do not turn the
   discussion into candidate screening, interview diligence, or risk
   assessment.

10. Do not manufacture disagreement, claims, refinements, or limitations
    merely to contribute another item or prolong the discussion.

11. A useful inference does not require majority agreement. A well-grounded
    insight raised by one stakeholder should survive if the evidence supports
    it.

12. When facts conflict on a detail, avoid building an inference that depends
    on choosing one unsupported version. Reason from the underlying pattern
    when possible.
"""


# ---------------------------------------------------------------------------
# Structured panel outputs
# ---------------------------------------------------------------------------
#
# This is the only structured output left anywhere in the panel. Every
# stakeholder round and the Mediator's own reasoning are free text — the one
# thing Python code actually needs in a checkable form is whether to keep
# looping, so that's the only thing still constrained to a schema.

class PanelCheckpoint(BaseModel):
    reasoning: str = Field(
        description=(
            "Your own free-form assessment of where the discussion stands: "
            "what's been covered, what's still worth probing, and why you're "
            "calling it complete or not. This is your thinking, not an "
            "instruction to the panel."
        )
    )

    next_round_prompt: str = Field(
        default="",
        description=(
            "ONE open, synthesizing question for the panel's next round — "
            "not a list of prior claims to react to. Do not enumerate "
            "specific stakeholders or specific claims by name for the panel "
            "to respond to one-by-one; that produces a mechanical reaction "
            "round instead of fresh thinking. Ask a real question whose "
            "answer requires reasoning across the discussion so far, the "
            "way you'd ask it out loud to the whole room. Sent to the panel "
            "verbatim. Leave empty only if discussion_complete is True."
        )
    )

    discussion_complete: bool = Field(
        description=(
            "True when another round is unlikely to surface a genuinely "
            "useful new career inference or meaningfully improve an existing "
            "one — not merely because some technically-unexamined angle "
            "still exists."
        )
    )


# ---------------------------------------------------------------------------
# Stakeholder agents
# ---------------------------------------------------------------------------

recruiter_agent = Agent(
    name="Recruiter",
    model="gpt-5.6-luna",
    instructions=f"""
You are the Recruiter on a career-analysis panel.

Use a market-signal lens. Look for capabilities, experiences, combinations of
background, and differentiators that would be legible and meaningful in the
job market.

Pay particular attention to:
- transferable experience
- unusually strong signals for the candidate's career stage
- coherent professional themes
- capabilities that may be valuable across multiple role families
- evidence that can be communicated clearly to employers

You are not screening the candidate in or out. Your job is to surface useful
career knowledge from a recruiter's perspective.

{COMMON_GUARDRAILS}
""",
)


hiring_manager_agent = Agent(
    name="Hiring Manager",
    model="gpt-5.6-sol",
    instructions=f"""
You are the Hiring Manager on a career-analysis panel.

Use a practical-capability lens. Ask what the candidate's history suggests
they can actually take ownership of, execute, solve, coordinate, or learn in
a work environment.

Pay particular attention to:
- practical execution
- problem solving
- ownership
- project delivery
- prioritization
- requirements translation
- operational judgment
- transferable capability across domains

Do not reduce your analysis to whether the candidate is immediately ready for
a particular job level. Extract the capabilities demonstrated by the evidence.

{COMMON_GUARDRAILS}
""",
)


technical_lead_agent = Agent(
    name="Technical Lead",
    model="gpt-5.6-terra",
    instructions=f"""
You are the Technical Lead on a career-analysis panel.

Use a technical-capability lens. Determine what the candidate's projects,
coursework, technical decisions, implementation details, and problem-solving
patterns actually demonstrate.

Pay particular attention to:
- programming concepts
- software-engineering practices
- system decomposition
- data handling
- testing and validation
- architecture
- APIs and integrations
- ML/AI workflow understanding
- debugging and technical reasoning
- evidence of increasing technical sophistication

Be rigorous about technical claims, but use that rigor to establish the
strongest accurate inference the evidence supports rather than turning missing
production evidence into standalone negative findings.

{COMMON_GUARDRAILS}
""",
)


people_leadership_agent = Agent(
    name="People & Leadership",
    model="gpt-5.6-luna",
    instructions=f"""
You are the People & Leadership stakeholder on a career-analysis panel.

Use a people, communication, and leadership lens.

Pay particular attention to:
- leadership
- delegation
- coordination
- client communication
- facilitation
- mentoring or coaching
- trust and responsibility
- conflict or expectation management
- reliability
- adaptability
- communicating across technical and nontechnical contexts

Look for demonstrated patterns that transfer across industries and roles.

Do not turn logistics, scheduling availability, or hiring administration into
career inferences.

{COMMON_GUARDRAILS}
""",
)


executive_agent = Agent(
    name="Executive",
    model="gpt-5.6-sol",
    instructions=f"""
You are the Executive on a career-analysis panel.

Use an ownership, strategy, business, and leverage lens.

Pay particular attention to:
- initiative
- self-direction
- entrepreneurial ability
- commercial judgment
- decision making
- risk ownership
- customer understanding
- product instincts
- resource allocation
- strategic thinking
- ability to connect business needs with execution
- unusual combinations of skills that may create professional leverage

Look across the candidate's entire history rather than treating each job,
project, or academic experience independently.

{COMMON_GUARDRAILS}
""",
)


STAKEHOLDERS = [
    recruiter_agent,
    hiring_manager_agent,
    technical_lead_agent,
    people_leadership_agent,
    executive_agent,
]


# ---------------------------------------------------------------------------
# Mediator
# ---------------------------------------------------------------------------

mediator_agent = Agent(
    name="Mediator",
    model="gpt-5.6-terra",
    instructions=f"""
You are the Mediator for a multi-agent career-analysis panel.

The panel's objective is to extract the richest defensible catalogue of career
knowledge from the candidate's facts.

You have two jobs.


CHECKPOINTING

After each round, determine whether another round is likely to produce:

- a genuinely new and useful career inference,
- a meaningful connection between facts that nobody has surfaced,
- a useful decomposition of an overly broad claim,
- a materially stronger version of an existing claim, or
- an important correction to an inference that currently overstates the
  evidence.

Do not continue merely because some topic or fact has not been mentioned.

Do not send the panel searching for:
- missing credentials
- hypothetical deficiencies
- interview questions
- hiring risks
- availability questions
- resume inconsistencies
- things the candidate has not demonstrated
- every possible technology or competency absent from the evidence

Those are not productive missing angles unless they materially change the
scope of an otherwise useful career inference.

A panel is complete when another round is unlikely to add distinct career
knowledge or materially improve the catalogue.


FINAL SYNTHESIS

Build the most complete useful catalogue supported by the discussion.

The final catalogue should:

1. Preserve strong insights even when only one stakeholder identified them.

2. Merge true duplicates.

3. Split broad umbrella entries when distinct competencies deserve independent
   representation.

4. Connect facts across different parts of the candidate's history when the
   combination supports a meaningful inference.

5. Prefer specific capabilities over generic labels.

6. Preserve appropriate uncertainty through explicit confidence and boundaries.

7. Exclude standalone entries whose primary content is missing evidence,
   unverified readiness, resume inconsistency, availability, hiring diligence,
   or absence of some competency.

8. Remove prior catalogue entries that no longer meet these standards, even if
   they are technically still true.

9. Treat the previous catalogue as provisional working memory, not as material
   that must automatically be preserved.

10. Optimize for usefulness to future career reasoning, resume development,
    interview preparation, job matching, and understanding the candidate's
    professional capabilities.

The final output must be human-readable Markdown.

Organize related inferences under useful category headings.

For each inference include:

- the inference itself
- confidence: high, moderate, or low
- the specific supporting evidence
- why the inference matters professionally
- a boundary only when one is genuinely needed to keep the claim accurate

Do not include a boundary merely for completeness.

{COMMON_GUARDRAILS}
""",
)


# ---------------------------------------------------------------------------
# Structured stream helper
# ---------------------------------------------------------------------------

async def _run_structured(result_stream):
    """
    Drain a streamed structured run without printing raw JSON deltas and
    return the parsed Pydantic output.
    """
    async for _ in result_stream.stream_events():
        pass

    return result_stream.final_output


# ---------------------------------------------------------------------------
# Panel dialogue
# ---------------------------------------------------------------------------

async def run_stakeholder_dialogue(
    facts_content: str,
    previous_synthesis: str = "",
) -> str:
    """
    Run independent stakeholder reads followed by mediator-directed panel
    rounds, then produce the final human-readable career inference catalogue.
    """

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    shared_session = SQLiteSession(
        f"intake_dialogue_{timestamp}",
        db_path=INTAKE_SESSIONS_DB,
    )

    async with responses_websocket_session() as ws:

        # -------------------------------------------------------------------
        # Round 1: independent stakeholder reads
        # -------------------------------------------------------------------

        for agent in STAKEHOLDERS:
            solo_session = SQLiteSession(
                f"intake_{agent.name}_{timestamp}_r1"
            )

            prompt = f"""
Here are the candidate's facts:

{facts_content}

From your perspective as {agent.name}, independently mine these facts for
career-relevant knowledge.

Surface the strongest and most useful inferences you can support.

Look beyond literal restatement. Connect facts when their combination reveals
a meaningful capability or professional pattern.

Decompose broad experiences into distinct competencies when appropriate.

Do not evaluate whether the candidate should be hired.

Do not create entries whose primary meaning is that something is missing,
unproven, unverified, or requires clarification.

You have not seen any other stakeholder's analysis yet.
"""

            print(f"\n[{agent.name} — round 1]")

            await stream_and_print(
                ws.run_streamed(
                    agent,
                    prompt,
                    session=solo_session,
                )
            )

            await shared_session.add_items(
                await solo_session.get_items()
            )

        # -------------------------------------------------------------------
        # Round 2+: Mediator-directed discovery and refinement
        # -------------------------------------------------------------------

        round_num = 1

        while True:

            checkpoint_agent = mediator_agent.clone(
                output_type=PanelCheckpoint
            )

            checkpoint_prompt = f"""
Above is the full panel discussion through round {round_num}.

Would another round genuinely add something worth having — a distinct career
inference nobody's raised, or a real improvement to one already on the table?
Not "is there some topic nobody's technically touched yet" — almost anything
can be framed that way. The question is whether continuing is actually worth
it, or whether the panel has already said what it has to say.

If it's worth continuing: write next_round_prompt as ONE real, open question
you want the panel to respond to next — something specific enough to provoke
an actual answer, not a list of leftover topics to check off.

Do not write next_round_prompt as a set of individual claims to react to
("Recruiter said X, Technical Lead said Y — respond to each"). That produces
a mechanical point-by-point reaction round, not genuine further thinking. Ask
the single question you'd actually ask the room out loud.

If not: set discussion_complete=True and leave next_round_prompt empty.
"""

            checkpoint = await _run_structured(
                ws.run_streamed(
                    checkpoint_agent,
                    checkpoint_prompt,
                    session=shared_session,
                )
            )

            status = (
                "complete"
                if checkpoint.discussion_complete
                else "continuing"
            )

            print(
                f"\n[Mediator — checkpoint after round "
                f"{round_num}, {status}]"
            )

            print(checkpoint.reasoning)

            if checkpoint.discussion_complete:
                print(
                    f"\n[Intake Agent] Panel reached completion "
                    f"after round {round_num}."
                )
                break

            print(f"\n[Mediator — next round prompt]\n{checkpoint.next_round_prompt}")

            round_num += 1

            for agent in STAKEHOLDERS:

                prompt = f"""
Round {round_num}. From your perspective as {agent.name}:

{checkpoint.next_round_prompt}
"""

                print(f"\n[{agent.name} — round {round_num}]")

                await stream_and_print(
                    ws.run_streamed(
                        agent,
                        prompt,
                        session=shared_session,
                    )
                )

        # -------------------------------------------------------------------
        # Final synthesis
        # -------------------------------------------------------------------

        history_note = ""

        if previous_synthesis.strip():
            history_note = f"""
The previous career catalogue was:

{previous_synthesis}

Treat this previous catalogue as provisional working memory.

Keep entries that remain useful and supported.

Improve entries when the current discussion supports a stronger, more
specific, or better-grounded formulation.

Add newly discovered career knowledge.

Delete entries that are merely:
- verification concerns
- missing-proof statements
- hiring diligence
- availability issues
- resume inconsistencies
- duplicates
- statements about competencies the candidate has not demonstrated
- other material that does not belong in a career-knowledge catalogue

Do not preserve an old entry merely because it already exists.
"""

        prompt = f"""
Above is the complete panel discussion, including independent stakeholder
analysis, later rounds of discovery and refinement, and Mediator checkpoints.

{history_note}

Produce the final updated career_inferences.md content.

The goal is the richest defensible human-readable catalogue of useful career
knowledge supported by the candidate's facts and the panel's reasoning.

This is a career knowledge catalogue, not a hiring assessment.

Preserve well-grounded insights even when only one stakeholder identified them.

Merge true duplicates.

Decompose broad umbrella claims when the evidence supports multiple distinct
career-relevant competencies.

Connect evidence across jobs, projects, education, business experience, and
other parts of the candidate's history when those combinations support useful
inferences.

Do not create standalone entries whose primary meaning is:
- something is unverified
- something is unproven
- evidence is missing
- availability needs clarification
- resume information needs verification
- the candidate lacks evidence for some unrelated competency

When a limitation genuinely matters, attach it as a boundary to the useful
inference it limits.

Write the result as clear Markdown organized under useful category headings.

For each inference include:

- **Inference:** the specific career capability, competency, strength, pattern,
  or professional signal
- **Confidence:** high, moderate, or low
- **Evidence:** the specific facts supporting it
- **Career value:** why the inference matters professionally
- **Boundary:** only when a meaningful limitation is needed to keep the
  inference accurate

Do not include a Boundary field when none is needed.

Return the full updated catalogue. It will replace the previous
career_inferences.md entirely.
"""

        print("\n[Mediator — synthesis]")

        synthesis = await stream_and_print(
            ws.run_streamed(
                mediator_agent,
                prompt,
                session=shared_session,
            )
        )

    return synthesis


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

async def generate_inferences():

    # Consolidate PREVIOUS panel runs before starting this new one.
    await panel_consolidator(
        "Consolidate any new panel runs into the digest."
    )

    # Load the factual knowledge produced by Career Watcher.
    if not os.path.exists(FACTS_FILE):
        print(f"[Intake Agent] '{FACTS_FILE}' not found.")
        return

    with open(FACTS_FILE, "r", encoding="utf-8") as f:
        facts_content = f.read()

    if not facts_content.strip():
        print("[Intake Agent] facts.md is empty — nothing to infer from.")
        return

    # Load the existing human-readable career catalogue so this panel
    # can revise and expand it rather than blindly starting over.
    previous_synthesis = ""

    if os.path.exists(INFERENCES_FILE):
        with open(INFERENCES_FILE, "r", encoding="utf-8") as f:
            previous_synthesis = f.read()

    # Run the NEW panel.
    try:
        synthesis = await run_stakeholder_dialogue(
            facts_content,
            previous_synthesis,
        )

        if synthesis:
            with open(INFERENCES_FILE, "w", encoding="utf-8") as f:
                f.write(synthesis)

            print("\n[Intake Agent] career_inferences.md updated.")

    except Exception as e:
        print(
            f"[Intake Agent] Panel failed, "
            f"career_inferences.md left unchanged: {e}"
        )


if __name__ == "__main__":
    import asyncio

    asyncio.run(generate_inferences())