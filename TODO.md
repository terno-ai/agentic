
- Done: Commandline # Done
- Done: Review what tools have been implemented, implement remaining tools: 
    https://code.claude.com/docs/en/agent-sdk/overview#typescript

    Tool	What it does
    Read	Read any file in the working directory
    Write	Create new files
    Edit	Make precise edits to existing files
    Bash	Run terminal commands, scripts, git operations
    Monitor	Watch a background script and react to each output line as an event
    Glob	Find files by pattern (**/*.ts, src/**/*.py)
    Grep	Search file contents with regex
    WebSearch	Search the web for current information
    WebFetch	Fetch and parse web page content



# Memory
    - Review memory implementation
    - Create a Memory system - Q <> A
        - It must update the old memories
    - Create a retrospection learner
        - Create Tools and corresponding memories & skills based on interaction with whole environment
        - Create memories from interaction
        - Create memories from user's questions

# Multiworkspace
- There can be multiple workspaces in an installation # How does it impact the agent???
- Separate the docker containers

# Conversation persistance
- Save every session in db, defaults to SQLite

# Sharing artifacts, memories, skills with team

# Low - (re)Publishing conversation to web

# SDK 


- Implement a simple separate UI in react to force the separation of agent
- Adapters:
    - DB Interface
    - Update Emitter Reciever
    - MCP Registry
    - Skills registry
    - Memory 
    - Retrospector
    - Executor
        - Unsecure with user permissions
        - Docker
    - Hooks:
        - Recommendation

# Folder based on todays date

# Ability to visualize

# RAG Capability

# Publish 
- PIP Package
- Github Readme
- Create Skill

# Attachment support

# File Download

# Skills.md 
   It is a reference guide that developers drop into their project (as .agentic/prompt.md or AGENT.md) so the agentic agent knows exactly how to help them build systems with this SDK —
  complete patterns, recipes, and gotchas, written for LLM consumption.
