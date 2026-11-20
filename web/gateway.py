# 網頁版聊天室的轉接服務（gateway）
#
# 瀏覽器不能直接開 TCP socket，只能用 HTTP 和 WebSocket。
# 這個程式站在瀏覽器和原本的 auth / server 中間：
#
#   瀏覽器 ←WebSocket→ gateway ←TCP，一行一則 JSON→ auth / server
#
# 它做兩件事：
#   1. 用 HTTP 提供網頁（static/index.html）
#   2. 把瀏覽器透過 WebSocket 送來的動作，轉成原本的協定送給 auth / server，
#      再把聊天伺服器回傳的每一行轉送回瀏覽器
#
# auth 和 server 完全不用改，所以網頁版、Tkinter 版、終端機版可以在同一個聊天室裡聊天。
#
# 只用 Python 標準函式庫。WebSocket 的握手（handshake）和資料框（frame）
# 是照 RFC 6455 自己實作的，方便對照課程裡學到的 socket 概念。
#
# 和其他 client 一樣，每個瀏覽器分頁對應「一條到聊天伺服器的 TCP 連線」。
# 差別是使用者名稱由 gateway 在登入成功後自己填，瀏覽器沒辦法冒充別人。

import asyncio
import base64
import hashlib
import json
import os
import struct
from pathlib import Path

# ---------- 設定（在 docker-compose.yml 的 environment 裡改） ----------

AUTH_HOST = os.getenv("AUTH_HOST", "localhost")
AUTH_PORT = int(os.getenv("AUTH_PORT", "8080"))
CHAT_HOST = os.getenv("CHAT_HOST", "localhost")
CHAT_PORT = int(os.getenv("CHAT_PORT", "8081"))
WEB_PORT = int(os.getenv("WEB_PORT", "8082"))

# 聊天伺服器用「client 填的位址:埠」來區分聊天室。
# 所有網頁版使用者都會進到這一間；想和 Tkinter / 終端機版的人同一間，
# 就把它設成那些人登入時填的伺服器位址（預設 localhost，埠 8081）。
ROOM_HOST = os.getenv("ROOM_HOST", "localhost")
ROOM_PORT = os.getenv("ROOM_PORT", "8081")

STATIC_DIR = Path(__file__).parent / "static"
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"  # RFC 6455 規定的固定字串
MAX_FRAME_BYTES = 64 * 1024  # 瀏覽器送來的單一訊息上限
MAX_MESSAGE_CHARS = 500  # 一則聊天訊息的字數上限
MAX_USERNAME_CHARS = 20
TIMEOUT = 5  # 連到 auth / server 的等待秒數
TOPIC_SENDER = "辯論題"  # server 廣播辯論題時用的名稱，不能拿來當帳號

GREEN = "\033[32m"
RED = "\033[31m"
RESET = "\033[0m"


# ---------- WebSocket（RFC 6455） ----------


class WebSocketClosed(Exception):
    pass


class WebSocket:
    """伺服器端的 WebSocket 連線，只處理文字訊息。

    每則訊息會被包成一個 frame：
      第 1 個 byte：FIN（是否為最後一段）+ opcode（1 文字、8 關閉、9 ping、10 pong）
      第 2 個 byte：MASK（瀏覽器送來的一定要有）+ 長度
      長度 126 → 後面 2 bytes 才是真正長度；127 → 後面 8 bytes
      有 MASK 時，接著 4 bytes 的遮罩，資料要逐 byte 和遮罩做 XOR 才能還原
    """

    def __init__(self, reader, writer):
        self.reader = reader
        self.writer = writer
        self.closed = False

    async def read_frame(self):
        first, second = await self.reader.readexactly(2)
        fin = bool(first & 0x80)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            (length,) = struct.unpack("!H", await self.reader.readexactly(2))
        elif length == 127:
            (length,) = struct.unpack("!Q", await self.reader.readexactly(8))
        if length > MAX_FRAME_BYTES:
            raise WebSocketClosed("訊息太大")
        if not masked:
            raise WebSocketClosed("瀏覽器送來的資料沒有遮罩")
        mask = await self.reader.readexactly(4)
        payload = bytearray(await self.reader.readexactly(length))
        for i in range(length):
            payload[i] ^= mask[i % 4]
        return fin, opcode, bytes(payload)

    async def receive(self):
        """回傳下一則完整的文字訊息；連線關閉時丟出 WebSocketClosed。"""
        parts = []
        while True:
            fin, opcode, payload = await self.read_frame()
            if opcode == 0x8:  # 對方要關閉
                await self.close()
                raise WebSocketClosed("瀏覽器關閉了連線")
            if opcode == 0x9:  # ping：照規定回 pong
                self.write_frame(0xA, payload)
                await self.writer.drain()
                continue
            if opcode == 0xA:  # pong：不用處理
                continue
            if opcode not in (0x0, 0x1):  # 只支援文字訊息
                raise WebSocketClosed(f"不支援的資料類型 {opcode}")
            parts.append(payload)
            if sum(len(part) for part in parts) > MAX_FRAME_BYTES:
                raise WebSocketClosed("訊息太大")
            if fin:  # 一則訊息可能被拆成好幾段，收到最後一段才組回來
                return b"".join(parts).decode("utf-8", errors="replace")

    def write_frame(self, opcode, payload):
        """伺服器送出的資料不加遮罩。"""
        length = len(payload)
        if length < 126:
            header = struct.pack("!BB", 0x80 | opcode, length)
        elif length < 65536:
            header = struct.pack("!BBH", 0x80 | opcode, 126, length)
        else:
            header = struct.pack("!BBQ", 0x80 | opcode, 127, length)
        # 一次 write 寫完整個 frame，兩個 task 同時送訊息也不會交錯
        self.writer.write(header + payload)

    async def send_json(self, obj):
        if self.closed:
            return
        try:
            self.write_frame(0x1, json.dumps(obj, ensure_ascii=False).encode("utf-8"))
            await self.writer.drain()
        except ConnectionError:
            self.closed = True

    async def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.write_frame(0x8, b"")
            await self.writer.drain()
        except ConnectionError:
            pass
        self.writer.close()


# ---------- 原本的協定：一行一則 JSON ----------


def encode_line(obj):
    return (json.dumps(obj) + "\n").encode("utf-8")


async def authenticate(action, username, password):
    """向 auth server 註冊或登入，和 Tkinter 版的做法相同。

    回傳 (是否成功, 要顯示給使用者的訊息)。
    """
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(AUTH_HOST, AUTH_PORT), TIMEOUT
        )
    except (OSError, asyncio.TimeoutError):
        return False, "無法連線到驗證伺服器，請確認伺服器有啟動"

    async def ask(request):
        writer.write(encode_line(request))
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), TIMEOUT)
        if not line:
            raise ConnectionError
        return line.decode("utf-8", errors="ignore").rstrip("\r\n")

    try:
        if action == "signup":
            response = await ask({"type": "register", "username": username, "password": password})
            if "註冊成功" not in response:
                return False, response
        response = await ask({"type": "login", "username": username, "password": password})
        return response == "登入成功", response
    except asyncio.TimeoutError:
        return False, "驗證伺服器沒有回應"
    except OSError:
        return False, "與驗證伺服器的連線中斷"
    finally:
        writer.close()


def check_username(name):
    """網頁版對帳號的限制。冒號和空白會讓「名字: 內容」的訊息格式解析錯誤。"""
    if not name:
        return "帳號不能是空的"
    if len(name) > MAX_USERNAME_CHARS:
        return f"帳號最多 {MAX_USERNAME_CHARS} 個字"
    if name == TOPIC_SENDER:
        return "這個名稱保留給辯論題使用"
    if any(ch.isspace() or ch in ":：" or not ch.isprintable() for ch in name):
        return "帳號不能包含空白或冒號"
    return None


class Session:
    """一個瀏覽器分頁的狀態：登入前只能登入，登入後有一條到聊天伺服器的 TCP 連線。"""

    def __init__(self, ws):
        self.ws = ws
        self.username = None
        self.chat_reader = None
        self.chat_writer = None
        self.pump_task = None

    async def handle(self, data):
        msg_type = data.get("type")

        if self.username is None:
            if msg_type in ("login", "signup"):
                await self.login(msg_type, data)
            else:
                await self.ws.send_json({"type": "error", "message": "請先登入"})
            return

        if msg_type == "message":
            # 原本的協定是一行一則訊息，所以內容不能有換行
            text = str(data.get("text", "")).replace("\r", " ").replace("\n", " ").strip()
            if not text:
                return
            if len(text) > MAX_MESSAGE_CHARS:
                await self.ws.send_json(
                    {"type": "error", "message": f"訊息太長，最多 {MAX_MESSAGE_CHARS} 個字"}
                )
                return
            await self.send_to_chat("message", message=text)
        elif msg_type == "topic":
            await self.send_to_chat("topic")
        elif msg_type == "online":
            await self.send_to_chat("online")

    async def login(self, action, data):
        username = str(data.get("username", "")).strip()
        password = str(data.get("password", ""))
        problem = check_username(username) or (None if password else "密碼不能是空的")
        if problem:
            await self.ws.send_json({"type": "auth_result", "ok": False, "message": problem})
            return

        ok, message = await authenticate(action, username, password)
        if not ok:
            await self.ws.send_json({"type": "auth_result", "ok": False, "message": message})
            return

        try:
            self.chat_reader, self.chat_writer = await asyncio.wait_for(
                asyncio.open_connection(CHAT_HOST, CHAT_PORT, limit=1024 * 1024), TIMEOUT
            )
        except (OSError, asyncio.TimeoutError):
            await self.ws.send_json(
                {"type": "auth_result", "ok": False, "message": "登入成功，但無法連線到聊天伺服器"}
            )
            return

        self.username = username
        print(f"{GREEN}{username} logged in from the web{RESET}")
        await self.ws.send_json({"type": "auth_result", "ok": True, "username": username})
        # 和其他 client 一樣，進聊天室先要歷史訊息
        await self.send_to_chat("fetch")
        self.pump_task = asyncio.create_task(self.pump())

    async def send_to_chat(self, msg_type, **fields):
        """補上 username 與聊天室，送給聊天伺服器。username 由 gateway 決定，不信任瀏覽器。"""
        if self.chat_writer is None or self.chat_writer.is_closing():
            await self.ws.send_json(
                {"type": "error", "message": "與聊天伺服器的連線中斷，請重新整理頁面"}
            )
            return
        message = {"type": msg_type, "username": self.username, "ip": ROOM_HOST, "port": ROOM_PORT}
        message.update(fields)
        self.chat_writer.write(encode_line(message))
        await self.chat_writer.drain()

    async def pump(self):
        """背景 task：聊天伺服器每送來一行，就原封不動轉給瀏覽器。"""
        try:
            while True:
                line = await self.chat_reader.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="ignore").rstrip("\r\n")
                await self.ws.send_json({"type": "line", "text": text})
        except (OSError, ValueError):
            pass
        await self.ws.send_json({"type": "disconnected"})
        await self.ws.close()

    async def close(self):
        if self.pump_task is not None and self.pump_task is not asyncio.current_task():
            self.pump_task.cancel()
        if self.chat_writer is not None:
            self.chat_writer.close()  # 聊天伺服器會把這個人從聊天室移除
        if self.username:
            print(f"{self.username} left the web chat")


# ---------- HTTP ----------


async def send_http(writer, status, content_type, body):
    reasons = {200: "OK", 400: "Bad Request", 404: "Not Found"}
    head = (
        f"HTTP/1.1 {status} {reasons[status]}\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Cache-Control: no-cache\r\n"
        "X-Content-Type-Options: nosniff\r\n"
        "Connection: close\r\n"
        "\r\n"
    )
    writer.write(head.encode("utf-8") + body)
    await writer.drain()


async def serve_websocket(reader, writer, headers):
    # 握手：把瀏覽器給的 key 加上固定字串，做 SHA-1 再轉 base64 回傳，證明我們看得懂 WebSocket
    key = headers["sec-websocket-key"]
    accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode("ascii")).digest()).decode()
    writer.write(
        (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n"
            "\r\n"
        ).encode("ascii")
    )
    await writer.drain()

    ws = WebSocket(reader, writer)
    session = Session(ws)
    try:
        while True:
            text = await ws.receive()
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                await session.handle(data)
    except (WebSocketClosed, asyncio.IncompleteReadError, ConnectionError):
        pass
    finally:
        await session.close()
        await ws.close()


async def handle_connection(reader, writer):
    """每個 HTTP 連線進來時呼叫：一般請求回傳網頁，/ws 升級成 WebSocket。"""
    try:
        try:
            raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, asyncio.TimeoutError):
            return

        request_line, *header_lines = raw.decode("latin-1").split("\r\n")
        parts = request_line.split()
        if len(parts) < 2:
            await send_http(writer, 400, "text/plain; charset=utf-8", b"Bad Request")
            return
        method, path = parts[0], parts[1].split("?", 1)[0]
        headers = {}
        for line in header_lines:
            if ":" in line:
                name, value = line.split(":", 1)
                headers[name.strip().lower()] = value.strip()

        if (
            path == "/ws"
            and headers.get("upgrade", "").lower() == "websocket"
            and "sec-websocket-key" in headers
        ):
            await serve_websocket(reader, writer, headers)
        elif method == "GET" and path in ("/", "/index.html"):
            body = (STATIC_DIR / "index.html").read_bytes()
            await send_http(writer, 200, "text/html; charset=utf-8", body)
        else:
            await send_http(writer, 404, "text/plain; charset=utf-8", b"Not Found")
    except ConnectionError:
        pass
    except Exception as e:
        print(f"{RED}Error while handling a connection: {e}{RESET}")
    finally:
        writer.close()


async def main():
    server = await asyncio.start_server(handle_connection, "0.0.0.0", WEB_PORT)
    print(f"{GREEN}Web gateway is listening on port {WEB_PORT}{RESET}")
    print(
        f"auth={AUTH_HOST}:{AUTH_PORT}  chat={CHAT_HOST}:{CHAT_PORT}  "
        f"room={ROOM_HOST}:{ROOM_PORT}"
    )
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Shutting down web gateway...")
