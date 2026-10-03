from dotenv import load_dotenv
load_dotenv()
import asyncio
import os
import re
import sys

from azure.identity import DefaultAzureCredential
from langchain_azure_ai.chat_models import AzureAIOpenAIApiChatModel
from langchain_azure_ai.tools import AzureAIProjectToolbox
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.graph import StateGraph, START, MessagesState
from langgraph.prebuilt import ToolNode, tools_condition

from tools import (
    get_production_performance,
    get_line_performance,
    get_downtime_events,
    get_machine_events,
    get_quality_defects,
    get_inventory_status,
    get_maintenance_history,
)

DATA_NOTE = (
    " Data available: daily production targets and actuals per plant and line; "
    "downtime events (timestamp, line, machine, minutes, reason code); machine "
    "details; quality defects per machine per day; inventory versus reorder point "
    "per plant, item and day; maintenance schedules, service records and work "
    "orders per machine. Not available: hourly or shift data, SKU or product data, "
    "OEE, sensor data, operator data, alarm logs."
)

SYSTEM_PROMPT = (
    "You are a manufacturing operations analyst for a client with three plants: "
    "Plant 1, Plant 2 and Plant 3. Production data covers 2026-09-02 to 2026-10-01, "
    "and you should treat 2026-10-01 as today. Use the tools to get facts. Never "
    "guess or invent numbers, and report figures exactly as the tools return them. "
    "If the user gives a date withomout a year, assume 2026. If a tool returns an "
    "error or no data, say so. If something is not available, say so plainly, and "
    "do not estimate, approximate or calculate a substitute for it from other data. "
    "Do not repeat the same tool call, and use at most 8 tool calls per question. "
    "For simple questions, answer in a sentence or two. For any question about causes "
    "or explanations (why, what caused it, what went wrong, was it only, is it likely), check "
    "the size of the miss, which line it came from, what stopped production, the "
    "maintenance history of any machine that stopped more than once, and inventory. "
    "Then answer briefly: the main cause, the evidence with exact figures, and any "
    "contributing factors."
) + DATA_NOTE


PROJECT_ENDPOINT = (
    os.environ.get("AZURE_AI_PROJECT_ENDPOINT") or os.environ["FOUNDRY_PROJECT_ENDPOINT"]
)

model = AzureAIOpenAIApiChatModel(
    project_endpoint=PROJECT_ENDPOINT,
    credential=DefaultAzureCredential(),
    model=os.environ.get("MODEL_DEPLOYMENT_NAME", "gpt-5-mini"),
)

tools = [tool(f) for f in (
    get_production_performance,
    get_line_performance,
    get_downtime_events,
    get_machine_events,
    get_quality_defects,
    get_inventory_status,
    get_maintenance_history,
)]


def _error_details(e):
    """Flatten grouped async errors so the log shows the real reason."""
    subs = getattr(e, "exceptions", None)
    if not subs:
        return [f"{type(e).__name__}: {e}"]
    return [d for s in subs for d in _error_details(s)]


def _toolbox_tools():
    """Load the SOP knowledge base tool from the Foundry toolbox named in TOOLBOX_NAME."""
    name = os.environ.get("TOOLBOX_NAME")
    if not name:
        return []
    try:
        toolbox = AzureAIProjectToolbox(
            project_endpoint=PROJECT_ENDPOINT,
            toolbox_name=name,
            credential=DefaultAzureCredential(),
        )
        found = asyncio.run(toolbox.get_tools())
    except Exception as e:  # keep the data tools working even if the toolbox is down
        print(f"WARNING: toolbox '{name}' not loaded: {' | '.join(_error_details(e))}", flush=True)
        return []
    for t in found:
        # Model providers only accept letters, digits, _ and - in tool names.
        t.name = re.sub(r"[^a-zA-Z0-9_-]", "_", t.name)
    return found


SOP_NOTE = (
    " The plant's standard operating procedures (SOPs) are searchable with the SOP "
    "knowledge base tool. For questions about what the written procedure requires, such "
    "as service intervals, required actions or escalation, use it and name the SOP "
    "document you used. SOPs describe rules, not what happened, so use the data tools "
    "for facts about events."
)

toolbox_tools = _toolbox_tools()
if toolbox_tools:
    tools += toolbox_tools
    SYSTEM_PROMPT += SOP_NOTE

model_with_tools = model.bind_tools(tools)


def agent(state: MessagesState):
    messages = [SystemMessage(SYSTEM_PROMPT)] + state["messages"]
    return {"messages": [model_with_tools.invoke(messages)]}


builder = StateGraph(MessagesState)
builder.add_node("agent", agent)
builder.add_node("tools", ToolNode(tools))
builder.add_edge(START, "agent")
builder.add_conditional_edges("agent", tools_condition)  # tool call -> tools, else END
builder.add_edge("tools", "agent")
graph = builder.compile()


def text_of(message):
    c = message.content
    if isinstance(c, str):
        return c
    return "".join(b.get("text", "") for b in c
                   if isinstance(b, dict) and b.get("type") == "text")


def ask(question):
    result = graph.invoke({"messages": [HumanMessage(question)]})
    for m in result["messages"]:
        for c in getattr(m, "tool_calls", None) or []:
            print(f"TOOL CALL: {c['name']} {c['args']}")
        if type(m).__name__ == "ToolMessage":
            print(f"TOOL RESULT: {str(m.content)[:300]}")
    print("\nANSWER:\n" + text_of(result["messages"][-1]))


if __name__ == "__main__":
    ask(sys.argv[1] if len(sys.argv) > 1 else "How did Plant 2 perform on October 1?")