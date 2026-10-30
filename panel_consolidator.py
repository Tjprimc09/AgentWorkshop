import os
import sys
from dotenv import load_dotenv
from agents import Agent, Runner
from agents.mcp import MCPServerStdio

load_dotenv()


panel_consolidator_agent = Agent(
    name="Panel Consolidator",
    model="gpt-5.6-luna",
    instructions="""You are Panel Consolidator, responsible for maintaining a running digest of
past career-inference panel runs, so future runs — and anyone reviewing this
project — have visibility into how the panel's conclusions have evolved,
without reading full deliberation transcripts.

You will receive two things: (1) the current digest (may be empty, if this
is the first run), and (2) one new panel run's full transcript to fold in —
every stakeholder's initial read, every reaction round, every Mediator
checkpoint, and the final synthesis.

Rules:
1. Update the digest to reflect what's new or notable: what the panel
   concluded, what got disputed and how (or whether) it resolved, any
   checkpoint that flagged a real gap, and anything that changed from a
   previous run's conclusions.
2. Do not simply append — integrate. If this run's conclusions update,
   resolve, or contradict something already in the digest, revise that
   entry rather than leaving both versions side by side.
3. Note process-level patterns worth knowing about too, not just
   conclusions — e.g. if the panel consistently disputes the same claim
   across multiple runs, or repeatedly hits MAX_ROUNDS without reaching
   genuine completion.
4. Never invent or infer details beyond what the transcript actually
   contained.
5. Output the full updated digest, not just what changed — this replaces
   the previous digest entirely.
"""
)

async def panel_consolidator(prompt: str) -> str:
    """Connects panel_consolidator_agent to pc_mcp_server.py, runs it on the
    given prompt with list_unconsolidated_panel_sessions/read_panel_session/
    mark_panel_session_consolidated/get_panel_digest/save_panel_digest
    available, and returns its final answer. Connects fresh and disconnects
    every call — no connection is held open between calls."""
    server_path = os.path.join(os.path.dirname(__file__), "pc_mcp_server.py")
    async with MCPServerStdio(
        name="panel_consolidator",
        params={"command": sys.executable, "args": [server_path]},
        client_session_timeout_seconds = 600,
    ) as server:
        panel_consolidator_agent.mcp_servers = [server]
        result = await Runner.run(panel_consolidator_agent, prompt)
        return result.final_output
