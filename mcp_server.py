from mcp.server.fastmcp import FastMCP

# check_career_documents was removed from here — Career Watcher now runs
# automatically at the start of every agent.py session instead of being a
# tool Job Getter calls mid-conversation. No tools are registered here
# currently; kept as a skeleton for whatever comes next (e.g. job search).

mcp = FastMCP("job_getter")


if __name__ == "__main__":
    mcp.run()
