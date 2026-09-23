"""Small asynchronous app-server transport; no credentials or prompts in logs."""

import asyncio
import json


class CodexRPC:
    def __init__(self, executable="codex"):
        self.executable = executable
        self.pending = {}
        self.events = asyncio.Queue()
        self.sequence = 0
        self.blocked = False

    async def start(self):
        self.process = await asyncio.create_subprocess_exec(
            self.executable, "app-server", "--listen", "stdio://",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=8 * 1024 * 1024,
        )
        self.reader = asyncio.create_task(self.read())
        await self.request("initialize", {
            "clientInfo": {"name": "interagents_monitor", "version": "0.1.0"}
        })
        await self.write({"method": "initialized", "params": {}})

    async def write(self, message):
        self.process.stdin.write((json.dumps(message) + "\n").encode())
        await self.process.stdin.drain()

    async def request(self, method, params):
        self.sequence += 1
        request_id = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self.write({"id": request_id, "method": method, "params": params})
            return await asyncio.wait_for(future, 60)
        finally:
            self.pending.pop(request_id, None)

    async def read(self):
        try:
            while line := await self.process.stdout.readline():
                await self.dispatch(json.loads(line))
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(RuntimeError("app-server disconnected"))
            await self.events.put({"method": "bridge/disconnected"})

    async def dispatch(self, message):
        if "id" in message and "method" in message:
            self.blocked = True
            method = message["method"]
            if method in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval"):
                response = {"result": {"decision": "decline"}}
            elif method == "item/permissions/requestApproval":
                response = {"result": {"permissions": {}, "scope": "turn"}}
            elif method == "item/tool/requestUserInput":
                response = {"result": {"answers": {}}}
            elif method == "mcpServer/elicitation/request":
                response = {"result": {"action": "decline"}}
            else:
                response = {"error": {"code": -32601, "message": "Interactive request unsupported"}}
            await self.write({"id": message["id"], **response})
        elif "id" in message:
            future = self.pending.get(message["id"])
            if future is not None and not future.done():
                if "error" in message:
                    future.set_exception(RuntimeError("app-server rejected request"))
                else:
                    future.set_result(message["result"])
        elif message.get("method") in ("item/completed", "turn/completed"):
            await self.events.put(message)

    async def run_turn(self, thread_id, prompt):
        self.blocked = False
        result = await self.request("turn/start", {
            "threadId": thread_id, "input": [{"type": "text", "text": prompt}]
        })
        turn_id = result["turn"]["id"]
        messages = []
        while True:
            event = await self.events.get()
            if event["method"] == "bridge/disconnected":
                raise RuntimeError("app-server disconnected")
            params = event.get("params", {})
            if params.get("threadId") != thread_id:
                continue
            if event["method"] == "item/completed" and params.get("turnId") == turn_id:
                item = params.get("item", {})
                if item.get("type") == "agentMessage":
                    messages.append(item)
            if event["method"] == "turn/completed" and params["turn"]["id"] == turn_id:
                if params["turn"].get("status") != "completed":
                    raise RuntimeError("Codex turn did not complete")
                final = [m for m in messages if m.get("phase") == "final_answer"]
                answer = "\n".join(m.get("text", "") for m in (final or messages[-1:]))
                if self.blocked:
                    answer = "Requiere intervención: se denegó una solicitud interactiva o de permisos.\n" + answer
                return answer or "Turno completado sin respuesta de texto."

    async def close(self):
        if not hasattr(self, "process"):
            return
        if self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 10)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
        await asyncio.gather(self.reader, return_exceptions=True)
