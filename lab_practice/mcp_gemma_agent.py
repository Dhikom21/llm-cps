"""
mcp_gemma_agent.py -- drive your vLLM Gemma as an agent over the Level 7 GUARDED
tools, via MCP (langchain-mcp-adapters).

It launches mcp_level7_server.py, loads its tools, and runs a perceive->reason->
act loop with Gemma. Two modes:

  MODE=native  -- uses OpenAI-style tool-calling (needs vLLM started with
                  --enable-auto-tool-choice + a Gemma tool-call parser).
  MODE=prompt  -- fallback: Gemma replies with JSON {"tool","args"} which we
                  parse and execute. Works on ANY OpenAI-compatible server
                  (this is what your level6 already relies on). DEFAULT.

Install:
    pip install langchain-openai langchain-mcp-adapters langgraph mcp

Prereqs running: BuildSim, docker compose (TimescaleDB), level4_consumer.py,
and level4_sensor.py (so 'level4-heater-A109-state' -- the whitelisted heater --
exists and A109 readings flow).

Run:
    python mcp_gemma_agent.py
    MODE=native python mcp_gemma_agent.py
    GOAL="Keep A109 in 20-23C" python mcp_gemma_agent.py
"""
import asyncio
import json
import os
import sys

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_mcp_adapters.client import MultiServerMCPClient

VLLM_URL   = os.environ.get("VLLM_URL", "http://carbon.eislab.se:8000/v1")
MODEL      = os.environ.get("MODEL", "google/gemma-4-E4B-it")
MODE       = os.environ.get("MODE", "prompt").lower()      # 'prompt' | 'native'
MAX_HOPS   = int(os.environ.get("MAX_HOPS", "6"))
GOAL       = os.environ.get("GOAL",
    "Room A109 should stay within 20-23 C. Read A109's temperature, and if it is "
    "below 20 turn the heater 'level4-heater-A109-state' on, if above 23 turn it "
    "off (always give a short reason). Then give a one-line summary.")

HERE = os.path.dirname(os.path.abspath(__file__))

llm = ChatOpenAI(model=MODEL, base_url=VLLM_URL, api_key="not-needed",
                 temperature=0.1)   # low temp for control decisions


def _client():
    return MultiServerMCPClient({
        "buildsim_l7": {
            "command": sys.executable,                       # same venv python
            "args": [os.path.join(HERE, "mcp_level7_server.py")],
            "transport": "stdio",
        }
    })


# ---------------- native tool-calling mode ----------------
async def run_native(tools):
    from langgraph.prebuilt import create_react_agent
    agent = create_react_agent(llm, tools)
    result = await agent.ainvoke({"messages": [("user", GOAL)]})
    print("\n=== FINAL ===")
    print(result["messages"][-1].content)


# ---------------- prompt-based fallback mode ----------------
def _tools_doc(tools):
    lines = []
    for t in tools:
        args = getattr(t, "args", {}) or {}
        arglist = ", ".join(args.keys())
        lines.append(f"- {t.name}({arglist}): {t.description.strip().splitlines()[0]}")
    return "\n".join(lines)

def _extract_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    s, e = text.find("{"), text.rfind("}")
    return text[s:e + 1] if s != -1 and e != -1 else text

async def run_prompt(tools):
    by_name = {t.name: t for t in tools}
    system = (
        "You are a building HVAC agent. You control a room via these tools:\n"
        f"{_tools_doc(tools)}\n\n"
        "Each turn, reply with ONE JSON object and nothing else:\n"
        '  to call a tool:  {\"tool\": \"<name>\", \"args\": { ... }}\n'
        '  when finished:   {\"final\": \"<short summary>\"}\n'
        "Rules: read before you act; set_actuator needs a 'reason' >= 5 chars; "
        "never invent actuator ids."
    )
    messages = [SystemMessage(system), HumanMessage(GOAL)]

    for hop in range(MAX_HOPS):
        resp = await llm.ainvoke(messages)
        raw = _extract_json(resp.content or "")
        try:
            d = json.loads(raw)
        except Exception:
            print(f"[hop {hop}] non-JSON reply -> stopping:\n{resp.content}")
            return
        if "final" in d:
            print("\n=== FINAL ===\n" + str(d["final"]))
            return
        name, args = d.get("tool"), d.get("args", {})
        tool = by_name.get(name)
        print(f"[hop {hop}] action: {name}({json.dumps(args)[:120]})")
        if tool is None:
            obs = {"error": f"unknown tool {name}"}
        else:
            obs = await tool.ainvoke(args)
        print(f"          observation: {str(obs)[:160]}")
        messages.append(HumanMessage(
            f"Result of {name}: {json.dumps(obs, default=str)[:800]}\n"
            "Decide the next step (JSON only)."))
    print("\n[reached MAX_HOPS]")


async def main():
    print(f"MCP-Gemma agent | model={MODEL} @ {VLLM_URL} | mode={MODE}")
    print(f"goal: {GOAL}\n")
    client = _client()
    tools = await client.get_tools()
    print("loaded tools:", ", ".join(t.name for t in tools), "\n")
    if MODE == "native":
        try:
            await run_native(tools)
        except Exception as e:
            print(f"[native mode failed: {e}]\n[falling back to prompt mode]\n")
            await run_prompt(tools)
    else:
        await run_prompt(tools)


if __name__ == "__main__":
    asyncio.run(main())
