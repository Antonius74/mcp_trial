import argparse
import json
import os
import shlex
import subprocess
from typing import Any, Dict, List, Optional

import httpx
from dotenv import load_dotenv


load_dotenv()

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gpt-oss:120b-cloud")
DEFAULT_MCP_SERVER_COMMAND = ".venv/bin/python" if os.path.exists(".venv/bin/python") else "python3"
MCP_SERVER_COMMAND = os.getenv("MCP_SERVER_COMMAND", DEFAULT_MCP_SERVER_COMMAND)
MCP_SERVER_ARGS = shlex.split(os.getenv("MCP_SERVER_ARGS", "-m mcp_server.server"))
PROTOCOL_VERSION = "2024-11-05"
MAX_TOOL_LOOPS = 8

SYSTEM_PROMPT = (
    "Sei un assistente che può usare tool MCP per leggere/scrivere clienti e ordini. "
    "Usa i tool quando servono dati reali dalle API. "
    "Rispondi in italiano in modo chiaro e sintetico."
)


class MCPStdIOClient:
    def __init__(self, command: str, args: List[str]) -> None:
        self.command = command
        self.args = args
        self.process: Optional[subprocess.Popen] = None
        self._request_id = 0

    def __enter__(self) -> "MCPStdIOClient":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def start(self) -> None:
        if self.process is not None:
            return

        cmd = [self.command] + self.args
        self.process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.initialize()

    def close(self) -> None:
        if self.process is None:
            return

        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()

        self.process = None

    def _send_message(self, payload: Dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None:
            raise RuntimeError("MCP process non avviato")

        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        header = f"Content-Length: {len(raw)}\r\n\r\n".encode("utf-8")
        self.process.stdin.write(header)
        self.process.stdin.write(raw)
        self.process.stdin.flush()

    def _read_message(self) -> Dict[str, Any]:
        if self.process is None or self.process.stdout is None:
            raise RuntimeError("MCP process non avviato")

        headers: Dict[str, str] = {}
        while True:
            line = self.process.stdout.readline()
            if not line:
                stderr_text = ""
                if self.process.stderr is not None:
                    try:
                        stderr_text = self.process.stderr.read().decode("utf-8", errors="ignore")
                    except Exception:
                        stderr_text = ""
                raise RuntimeError(f"MCP server terminato. STDERR: {stderr_text}")
            if line in (b"\r\n", b"\n"):
                break
            key, _, value = line.decode("utf-8").partition(":")
            headers[key.strip().lower()] = value.strip()

        content_length = int(headers.get("content-length", "0"))
        if content_length <= 0:
            raise RuntimeError("Messaggio MCP senza Content-Length valido")

        body = self.process.stdout.read(content_length)
        if not body:
            raise RuntimeError("Body MCP vuoto")

        return json.loads(body.decode("utf-8"))

    def _request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self._request_id += 1
        request_id = self._request_id

        self._send_message(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params or {},
            }
        )

        while True:
            message = self._read_message()
            if message.get("id") != request_id:
                continue
            if "error" in message:
                error = message["error"]
                raise RuntimeError(f"Errore MCP {error.get('code')}: {error.get('message')}")
            return message.get("result", {})

    def _notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        self._send_message(
            {
                "jsonrpc": "2.0",
                "method": method,
                "params": params or {},
            }
        )

    def initialize(self) -> None:
        self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "ollama-mcp-client", "version": "1.0.0"},
            },
        )
        self._notify("notifications/initialized", {})

    def list_tools(self) -> List[Dict[str, Any]]:
        result = self._request("tools/list", {})
        return result.get("tools", [])

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        return self._request(
            "tools/call",
            {
                "name": name,
                "arguments": arguments,
            },
        )


def _tool_to_ollama_schema(tool: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.get("name", ""),
            "description": tool.get("description", ""),
            "parameters": tool.get("inputSchema", {"type": "object", "properties": {}}),
        },
    }


def _parse_arguments(raw_args: Any) -> Dict[str, Any]:
    if raw_args is None:
        return {}
    if isinstance(raw_args, dict):
        return raw_args
    if isinstance(raw_args, str):
        candidate = raw_args.strip()
        if not candidate:
            return {}
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return {}
    return {}


def _tool_result_to_text(tool_result: Dict[str, Any]) -> str:
    content = tool_result.get("content", [])
    if not isinstance(content, list):
        return json.dumps(tool_result, ensure_ascii=False)

    parts: List[str] = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text", "")))
        else:
            parts.append(json.dumps(item, ensure_ascii=False))
    return "\\n".join(parts)


def _ollama_chat(messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "tools": tools,
        "stream": False,
    }

    with httpx.Client(timeout=90.0) as client:
        response = client.post(f"{OLLAMA_URL}/api/chat", json=payload)
        response.raise_for_status()
        data = response.json()

    message = data.get("message", {})
    if "role" not in message:
        message["role"] = "assistant"
    if "content" not in message:
        message["content"] = ""
    return message


def ask_with_tools(user_prompt: str, mcp_client: MCPStdIOClient) -> str:
    tools = mcp_client.list_tools()
    ollama_tools = [_tool_to_ollama_schema(tool) for tool in tools]

    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    for _ in range(MAX_TOOL_LOOPS):
        assistant_message = _ollama_chat(messages, ollama_tools)
        messages.append(assistant_message)

        tool_calls = assistant_message.get("tool_calls") or []
        if not tool_calls:
            return assistant_message.get("content", "")

        for tool_call in tool_calls:
            function = tool_call.get("function", {})
            tool_name = function.get("name")
            tool_args = _parse_arguments(function.get("arguments"))

            if not tool_name:
                continue

            tool_result = mcp_client.call_tool(tool_name, tool_args)
            tool_text = _tool_result_to_text(tool_result)

            tool_message: Dict[str, Any] = {
                "role": "tool",
                "name": tool_name,
                "content": tool_text,
            }
            if "id" in tool_call:
                tool_message["tool_call_id"] = tool_call["id"]
            messages.append(tool_message)

    return "Interrotto: superato il limite massimo di tool call iterative."


def run_cli() -> None:
    parser = argparse.ArgumentParser(
        description="Client MCP che usa Ollama per chiamare tool REST via MCP stdio.",
    )
    parser.add_argument(
        "prompt",
        nargs="*",
        help="Prompt utente (se omesso parte la modalità interattiva)",
    )
    args = parser.parse_args()

    with MCPStdIOClient(MCP_SERVER_COMMAND, MCP_SERVER_ARGS) as mcp_client:
        if args.prompt:
            prompt = " ".join(args.prompt).strip()
            answer = ask_with_tools(prompt, mcp_client)
            print(answer)
            return

        print("Modalità interattiva. Scrivi 'exit' per uscire.")
        while True:
            user_input = input("Tu> ").strip()
            if user_input.lower() in {"exit", "quit"}:
                break
            if not user_input:
                continue
            answer = ask_with_tools(user_input, mcp_client)
            print(f"LLM> {answer}")


if __name__ == "__main__":
    run_cli()
