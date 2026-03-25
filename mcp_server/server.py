import json
import os
import sys
from typing import Any, Callable, Dict, Optional

import httpx
from dotenv import load_dotenv


load_dotenv()

API_BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
SERVER_NAME = "misc-service"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSION = "2024-11-05"


def _call_api(method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    url = f"{API_BASE_URL}{path}"
    with httpx.Client(timeout=20.0) as client:
        response = client.request(method=method, url=url, json=payload)

    if response.status_code >= 400:
        return {
            "ok": False,
            "status_code": response.status_code,
            "error": response.text,
        }

    try:
        data = response.json()
    except ValueError:
        data = response.text

    return {"ok": True, "status_code": response.status_code, "data": data}


def tool_health_check(arguments: Dict[str, Any]) -> Dict[str, Any]:
    return _call_api("GET", "/health")


def tool_list_customers(arguments: Dict[str, Any]) -> Dict[str, Any]:
    return _call_api("GET", "/customers")


def tool_create_customer(arguments: Dict[str, Any]) -> Dict[str, Any]:
    return _call_api(
        "POST",
        "/customers",
        payload={
            "name": arguments.get("name"),
            "email": arguments.get("email"),
        },
    )


def tool_list_orders(arguments: Dict[str, Any]) -> Dict[str, Any]:
    return _call_api("GET", "/orders")


def tool_create_order(arguments: Dict[str, Any]) -> Dict[str, Any]:
    return _call_api(
        "POST",
        "/orders",
        payload={
            "customer_id": arguments.get("customer_id"),
            "item": arguments.get("item"),
            "amount": arguments.get("amount"),
            "status": arguments.get("status", "new"),
        },
    )


TOOL_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "health_check": {
        "description": "Ritorna lo stato della REST API mock.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "handler": tool_health_check,
    },
    "list_customers": {
        "description": "Legge tutti i clienti dal servizio REST.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "handler": tool_list_customers,
    },
    "create_customer": {
        "description": "Crea un nuovo cliente nel servizio REST.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Nome cliente"},
                "email": {"type": "string", "description": "Email cliente"},
            },
            "required": ["name", "email"],
            "additionalProperties": False,
        },
        "handler": tool_create_customer,
    },
    "list_orders": {
        "description": "Legge tutti gli ordini dal servizio REST.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "handler": tool_list_orders,
    },
    "create_order": {
        "description": "Crea un nuovo ordine nel servizio REST.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "integer", "description": "ID cliente"},
                "item": {"type": "string", "description": "Nome prodotto"},
                "amount": {"type": "number", "description": "Importo ordine"},
                "status": {"type": "string", "description": "Stato ordine"},
            },
            "required": ["customer_id", "item", "amount"],
            "additionalProperties": False,
        },
        "handler": tool_create_order,
    },
}


def _read_message() -> Optional[Dict[str, Any]]:
    headers: Dict[str, str] = {}

    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        key, _, value = line.decode("utf-8").partition(":")
        headers[key.strip().lower()] = value.strip()

    content_length = int(headers.get("content-length", "0"))
    if content_length <= 0:
        return None

    body = sys.stdin.buffer.read(content_length)
    if not body:
        return None

    return json.loads(body.decode("utf-8"))


def _send_message(payload: Dict[str, Any]) -> None:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    header = f"Content-Length: {len(raw)}\r\n\r\n".encode("utf-8")
    sys.stdout.buffer.write(header)
    sys.stdout.buffer.write(raw)
    sys.stdout.buffer.flush()


def _jsonrpc_response(message_id: Any, result: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def _jsonrpc_error(message_id: Any, code: int, text: str) -> Dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": message_id,
        "error": {"code": code, "message": text},
    }


def _handle_request(method: str, params: Dict[str, Any]) -> Any:
    if method == "initialize":
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }

    if method == "tools/list":
        tools = []
        for name, meta in TOOL_DEFINITIONS.items():
            tools.append(
                {
                    "name": name,
                    "description": meta["description"],
                    "inputSchema": meta["inputSchema"],
                }
            )
        return {"tools": tools}

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name not in TOOL_DEFINITIONS:
            raise ValueError(f"Tool non trovato: {name}")

        handler: Callable[[Dict[str, Any]], Dict[str, Any]] = TOOL_DEFINITIONS[name]["handler"]
        tool_result = handler(arguments)
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(tool_result, ensure_ascii=False),
                }
            ]
        }

    if method == "ping":
        return {}

    raise NotImplementedError(f"Metodo non supportato: {method}")


def main() -> None:
    while True:
        message = _read_message()
        if message is None:
            break

        message_id = message.get("id")
        method = message.get("method")
        params = message.get("params") or {}

        if not method:
            continue

        is_notification = message_id is None
        if is_notification:
            continue

        try:
            result = _handle_request(method, params)
            _send_message(_jsonrpc_response(message_id, result))
        except NotImplementedError as exc:
            _send_message(_jsonrpc_error(message_id, -32601, str(exc)))
        except Exception as exc:  # pylint: disable=broad-except
            _send_message(_jsonrpc_error(message_id, -32000, str(exc)))


if __name__ == "__main__":
    main()
