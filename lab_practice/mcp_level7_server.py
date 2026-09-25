"""
mcp_level7_server.py -- expose the Level 7 GUARDED tools as an MCP server.

Unlike buildingsim/mcp/server.py (which only wraps BuildSim's REST API), this
wraps level7_tools, so it ALSO:
  * reads TimescaleDB          (read_sensors, query_history)
  * enforces the set_actuator whitelist + required reason
  * writes the audit trail     (every call -> `decisions` table)

Any MCP client (Claude Desktop, the vLLM/Gemma bridge, LibreChat, Open WebUI)
gets the guarded, DB-aware toolset.

Install:  pip install mcp
Run (stdio, for Claude Desktop / the Gemma bridge):
    python mcp_level7_server.py
Run (SSE, for LibreChat / Open WebUI over the network):
    TRANSPORT=sse python mcp_level7_server.py       # serves http://<host>:8009/sse

Prereqs: BuildSim + TimescaleDB reachable (docker compose up -d).

NOTE: set_actuator only allows actuators on the whitelist in level7_tools.py
(ACTUATOR_RULES). Out of the box that's 'level4-heater-A109-state', so run
level4_sensor.py for a controllable heater. To let it drive the multi-room sim
heaters, add sim-A109/A110/A111-heater-state to ACTUATOR_RULES.
"""
import os
import sys
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

import db
import level7_tools as tools

AGENT_ID = "mcp-gemma-agent"

# host/port only used in SSE mode (TRANSPORT=sse), e.g. for LibreChat/Open WebUI.
mcp = FastMCP(
    "BuildSim-Level7",
    host=os.environ.get("MCP_HOST", "0.0.0.0"),
    port=int(os.environ.get("MCP_PORT", "8009")),
    instructions=(
        "Guarded building-control tools backed by TimescaleDB + BuildSim. "
        "read_sensors to perceive; set_actuator to act (reason required, whitelisted); "
        "create_alert for anomalies; query_history to introspect; "
        "highlight_rooms and find_route for the viewer and evacuation."
    ),
)


@mcp.tool()
def read_sensors(room: str, sensor_types: Optional[List[str]] = None,
                 last_seconds: int = 30) -> Dict[str, Any]:
    """Read recent sensor values for a room from TimescaleDB. Returns a per-type
    summary {current, mean, min, max, trend, samples}."""
    return tools.call(AGENT_ID, "read_sensors",
                      {"room": room, "sensor_types": sensor_types,
                       "last_seconds": last_seconds}).to_dict()


@mcp.tool()
def set_actuator(actuator_id: str, state: str, reason: str) -> Dict[str, Any]:
    """Command a building actuator (e.g. a heater on/off). Validated against a
    whitelist; unknown ids or illegal states are rejected. 'reason' is required
    (>=5 chars) and is audited."""
    return tools.call(AGENT_ID, "set_actuator",
                      {"actuator_id": actuator_id, "state": state,
                       "reason": reason}).to_dict()


@mcp.tool()
def create_alert(severity: str, message: str,
                 room: Optional[str] = None) -> Dict[str, Any]:
    """Record an alert. severity is 'info' | 'warning' | 'critical'."""
    return tools.call(AGENT_ID, "create_alert",
                      {"severity": severity, "message": message,
                       "room": room}).to_dict()


@mcp.tool()
def query_history(sql: str, max_rows: int = 50) -> Dict[str, Any]:
    """Run a READ-ONLY SELECT against TimescaleDB. Only SELECT is permitted."""
    return tools.call(AGENT_ID, "query_history",
                      {"sql": sql, "max_rows": max_rows}).to_dict()


@mcp.tool()
def highlight_rooms(rooms: List[Dict[str, Any]],
                    session_id: Optional[str] = None) -> Dict[str, Any]:
    """Colour rooms in the BuildSim 3D viewer. rooms = list of
    {room_id:int, color:'#hex', opacity:0..1}."""
    return tools.call(AGENT_ID, "highlight_rooms",
                      {"rooms": rooms, "session_id": session_id}).to_dict()


@mcp.tool()
def find_route(from_room: str, to_room: str,
               graph_type: str = "walkable") -> Dict[str, Any]:
    """Shortest walkable path between two rooms (evacuation/routing)."""
    return tools.call(AGENT_ID, "find_route",
                      {"from_room": from_room, "to_room": to_room,
                       "graph_type": graph_type}).to_dict()


if __name__ == "__main__":
    transport = os.environ.get("TRANSPORT", "stdio")

    if transport == "stdio":
        # In stdio mode STDOUT carries the MCP protocol. A stray print() (e.g. an
        # audit-failure message from level7_tools) would corrupt it, so send all
        # print() output to stderr and keep the protocol channel clean.
        import builtins
        _orig_print = builtins.print

        def _stderr_print(*a, **kw):
            kw.setdefault("file", sys.stderr)
            _orig_print(*a, **kw)

        builtins.print = _stderr_print

    try:
        db.ensure_schema()   # make sure decisions/alerts/readings tables exist
    except Exception as e:
        # Don't die if the DB is down: start anyway so the client can connect,
        # and let individual tools report the problem.
        print(f"[warn] could not ensure schema: {e}")

    if transport in ("sse", "streamable-http"):
        port = os.environ.get("MCP_PORT", "8009")
        path = "/sse" if transport == "sse" else "/mcp"
        print(f"MCP server ({transport}) on :{port}{path}")
        mcp.run(transport=transport)
    else:
        mcp.run()          # stdio (Claude Desktop / the Gemma bridge launch it this way)
