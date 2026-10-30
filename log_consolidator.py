import os
import sys
from dotenv import load_dotenv
from agents import Agent, Runner
from agents.mcp import MCPServerStdio

load_dotenv()


log_consolidator_agent = Agent(
    name="Log Consolidator",
    model="gpt-5.6-luna",
    instructions="""You are Log Consolidator, responsible for maintaining a running digest of
past conversations so future sessions have continuity without reading full
transcripts.

You will receive two things: (1) the current digest (may be empty, if this
is the first log), and (2) one new conversation transcript to fold in.

Rules:
1. Update the digest to reflect what's new or notable: decisions made,
   topics covered, open questions left unresolved — anything that would
   help a future conversation pick up with context instead of starting cold.
2. Do not simply append — integrate. If this conversation updates, resolves,
   or contradicts something already in the digest, revise that entry rather
   than leaving both versions side by side.
3. Omit routine or trivial exchanges (greetings, test messages, anything
   with no information worth remembering) rather than manufacturing
   significance for them.
4. Do not record tool failures, errors, or claims that a capability is
   "unavailable" / "timed out" as established facts. Those describe transient
   technical state at one point in time, not something true about the user or
   their situation — encoding them here just causes future sessions to wrongly
   assume something is still broken instead of trying it fresh.
5. Write in your own words as a working narrative, not excerpted transcript
   lines.
6. Only make inferences supported by what the conversation(s) actually
   contained.
7. Output the full updated digest, not just what changed — this replaces
   the previous digest entirely.
"""
)

async def log_consolidator(prompt: str) -> str:
    """Connects log_consolidator_agent to lc_mcp_server.py, runs it on the
    given prompt with list_unconsolidated_sessions/read_session/
    mark_consolidated/get_digest/save_digest available, and returns its
    final answer. Connects fresh and disconnects every call — no connection
    is held open between calls."""
    server_path = os.path.join(os.path.dirname(__file__), "lc_mcp_server.py")
    async with MCPServerStdio(
        name="log_consolidator",
        params={"command": sys.executable, "args": [server_path]},
        client_session_timeout_seconds = 600,
    ) as server:
        log_consolidator_agent.mcp_servers = [server]
        result = await Runner.run(log_consolidator_agent, prompt)
        return result.final_output
