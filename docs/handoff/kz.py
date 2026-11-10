"""Tiny client for the local Kanzo board (MCP over HTTP, localhost only). Used by the /loop session to read and update tasks.
Usage:  python3 -I tools/kz.py tools
        python3 -I tools/kz.py call <tool> '<json arguments>'
It talks only to http://127.0.0.1:4242 and holds no credentials."""
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:4242/mcp"
HEAD = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}


def _post(body, session=None):
    headers = dict(HEAD)
    if session:
        headers["Mcp-Session-Id"] = session
    req = urllib.request.Request(BASE, data=json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        text = r.read().decode()
        return r.headers.get("Mcp-Session-Id"), text


def _result(text):
    """The reply is server-sent events: take the last data line that holds JSON."""
    out = None
    for line in text.splitlines():
        if line.startswith("data:") and line[5:].strip().startswith("{"):
            out = json.loads(line[5:])
    return out


def session():
    sid, _ = _post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "box-builder-loop", "version": "1"}}})
    _post({"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)
    return sid


def main(argv):
    sid = session()
    if argv[:1] == ["tools"]:
        _, t = _post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, sid)
        for tool in _result(t)["result"]["tools"]:
            props = tool.get("inputSchema", {}).get("properties", {})
            print(f"{tool['name']}({', '.join(props)})\n    {tool.get('description', '')[:160]}")
    elif argv[:1] == ["call"] and len(argv) >= 2:
        args = json.loads(argv[2]) if len(argv) > 2 else {}
        _, t = _post({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": argv[1], "arguments": args}}, sid)
        res = _result(t)
        for part in (res.get("result", {}).get("content") or [{"text": json.dumps(res)}]):
            print(part.get("text", ""))
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
