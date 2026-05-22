from agentic import Agent, print_events

agent = Agent(model="gpt-5.5")

# Option 1 — stream_sync prints as tokens arrive, returns full text
response = agent.stream_sync("Build a REST API with FastAPI")

# Option 2 — handle events yourself
# async for event in agent.stream("..."):
#     print_events(event)
