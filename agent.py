

import asyncio # lets the program wait on slow things (typing, AI replies) without freezing
import os # for building file paths and checking if files exist
import sys # for sys.executable, so spawned subprocesses use this same venv's Python
from datetime import datetime # for timestamps (log file names, conversation IDs)
from dotenv import load_dotenv # loads secret settings (like the API key) from a .env file

# The three building blocks from the agents toolkit:
#   Agent — the AI assistant itself (name, model, instructions, tools)
#   responses_websocket_session — one open line to the AI service, reused for every message instead of reconnecting each time
#   SQLiteSession — remembers the back-and-forth of this conversation
from agents import Agent, responses_websocket_session, SQLiteSession
from agents.mcp import MCPServerStdio  # connects to mcp_server.py, the separate process with the real tools
from career_watcher import career_watcher # investigates new/changed career documents into facts.md
from panel import generate_inferences # runs the stakeholder panel over facts.md into career_inferences.md
from log_consolidator import log_consolidator # rolls past conversation logs into one running summary
from utils import stream_and_print # prints the AI's reply as it arrives, word by word

load_dotenv()  # actually loads the secret settings mentioned above

# ---- Building the AI's instructions -----------------------------------------
# This function builds the block of text that tells the AI who it is and what
# it already knows. It gets called once at startup and again after the resume
# scan / log summary refresh, so the AI always starts each run with the latest
# version of your profile and history baked into its instructions.

def get_agent_instructions():
    # The AI's fixed job description — this part never changes between runs.
    base_instructions = "You are a professional assistant that helps people get a job by learning their background, skills, and interests, searching online for jobs that would be a good fit for the user, tailoring resume versions and cover letters for the jobs you find, and optimizing professional profiles as needed."

    # Read the candidate's known profile (built up by save_candidate_fact /
    # the career-watcher pipeline), if it exists yet.
    profile_path = os.path.join(os.path.dirname(__file__), "Career", "profile.md")
    profile_content = ""
    if os.path.exists(profile_path):
        with open(profile_path, "r", encoding="utf-8") as f:
            profile_content = f.read()

    # Read the career facts/inferences built up by the Career Watcher agent,
    # if it's investigated anything yet.
    inferences_path = os.path.join(os.path.dirname(__file__), "career_inferences.md")
    inferences_content = ""
    if os.path.exists(inferences_path):
        with open(inferences_path, "r", encoding="utf-8") as f:
            inferences_content = f.read()

    # Read the running summary of past conversations, if one exists yet.
    digest_path = os.path.join(os.path.dirname(__file__), "logs", "digest.md")
    digest_content = ""
    if os.path.exists(digest_path):
        with open(digest_path, "r", encoding="utf-8") as f:
            digest_content = f.read()

    # Stitch it all together: start with the fixed job description, then tack
    # on the profile, the career-watcher findings, and the conversation
    # summary, but only if each one actually has content (an empty/
    # whitespace-only file adds nothing).
    instructions = base_instructions
    if profile_content.strip():
        instructions += f"\n\nHere is the candidate's current profile:\n{profile_content}"
    if inferences_content.strip():
        instructions += f"\n\nHere is what's been learned about the candidate from their career documents:\n{inferences_content}"
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
    return instructions

# creating an agent
agent = Agent(
    name = "Job Getter",
    model = "gpt-5.6-luna",
    instructions = get_agent_instructions(),
    )

# in-conversation memory using a 'session' — persisted to sessions.db (not
# the default :memory:) so log_consolidator can read past conversations
# back out of it after this process exits.
conversation_id = f"job_getter_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
db_path = os.path.join(os.path.dirname(__file__), "sessions.db")
session = SQLiteSession(conversation_id, db_path=db_path)

async def main():
    # ---- Flow of this function, in order ----
    # 1. Launch mcp_server.py and wait for the handshake to finish — nothing
    #    below can use the connection until this step is done. (It has no
    #    tools registered yet — kept connected for whatever comes next.)
    # 2. Attach that live connection to `agent`, so it can call MCP tools
    #    at any point during the conversation.
    # 3. Run the log consolidator.
    # 4. Run the career watcher (investigates any new/changed career
    #    documents into facts.md).
    # 5. Run the intake panel (this also consolidates any past panel runs
    #    into their own digest internally, before debating facts.md into
    #    career_inferences.md — self-contained, same as how career_watcher
    #    tracks its own already-processed documents).
    # 6. Refresh the agent's instructions with anything new from steps 3-5.
    # 7. Run the chat loop.
    # 8. Save the conversation to a log file.
    # The connection from step 1 has to stay open through all of steps 2-8 —
    # the chat loop in step 7 might call an MCP tool mid-conversation, so it
    # can't be closed early.
    # Steps 3-5 are mandatory, not model-decided: they always run before
    # this session starts, so Job Getter begins every run with up-to-date
    # long-term memory instead of waiting to be asked.

    server_path = os.path.join(os.path.dirname(__file__), "mcp_server.py")
    async with MCPServerStdio(
        name="main_agent",
        params={"command": sys.executable, "args": [server_path]},
        client_session_timeout_seconds = 600,
    ) as server:
        agent.mcp_servers = [server]

        print("Running Log Consolidator...")
        await log_consolidator("Consolidate any new conversations into the digest.")

        print("Running Career Watcher...")
        await career_watcher(
            "Investigate anything in list_unprocessed_documents and record your findings."
        )

        print("Running Intake Panel...")
        await generate_inferences()

        # Refresh agent instructions now that the digest, facts, and career
        # inferences may have just changed.
        agent.instructions = get_agent_instructions()

        print(f"\nWelcome to Job Getter!\nHow can we get started today? (stop the conversation any time by entering 'quit' or 'exit').")

        turns = [] #accumulates conversation turns for logging

        async with responses_websocket_session() as ws:
            while True:

                user_input = await asyncio.to_thread(input, "\nYou: ")

                if user_input.lower().strip() in ["quit", "exit"]: #checks to see if the user wants to exit
                    break
                print("\nJob Getter: ", end="")
                final_output = await stream_and_print(ws.run_streamed(agent, user_input, session=session))

                turns.append(("User", user_input))
                turns.append(("Job Getter", final_output))

        #save conversation to a markdown log
        if turns:
            try:
                log_dir = os.path.join(os.path.dirname(__file__), "logs")
                os.makedirs(log_dir, exist_ok=True)

                timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
                log_path = os.path.join(log_dir, f"{timestamp}.md")

                with open(log_path, "w", encoding="utf-8") as f:
                    f.write(f"# Conversation — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
                    for role, content in turns:
                        f.write(f"## {role}\n{content}\n\n")

                print(f"\nConversation saved to {log_path}")
            except Exception as e:
                print(f"\nWarning: Could not save conversation log: {e}")


if __name__ == "__main__":
    asyncio.run(main())