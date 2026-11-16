# 圖形化介面的聊天室 client（Tkinter）
#
# 和終端機版的 client.py 用同一套協定連到 auth 與 chat server：
#   - TCP socket
#   - 一行一則 JSON 訊息，用 "type" 欄位分辨動作
# 所以伺服器不用為了圖形介面另外開一套 API，兩種 client 可以同時在同一個聊天室裡。
#
# 執行方式（在自己的電腦上，不是在 Docker 裡）：
#   python3 gui_client.py
#
# 重點觀念：Tkinter 的畫面只能由「主執行緒」更新。
# 接收訊息的背景執行緒不能直接改畫面，所以它只把收到的內容丟進 queue，
# 主執行緒每 100 毫秒從 queue 取出來顯示（見 ChatWindow.poll_queue）。

import json
import os
import queue
import socket
import threading
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, scrolledtext, simpledialog

# 預設連到本機；要連別台電腦時，在登入畫面的「伺服器位址」改成對方的 IP
DEFAULT_HOST = os.getenv("SERVER_HOST", "localhost")
AUTH_PORT = int(os.getenv("AUTH_PORT", "8080"))
CHAT_PORT = int(os.getenv("CHAT_PORT", "8081"))

# 伺服器廣播辯論題與線上名單時使用的開頭，用來決定顯示的顏色
TOPIC_PREFIX = "辯論題: "
ONLINE_PREFIX = "[線上] "

TOPIC_COOLDOWN_MS = 3000  # 抽題按鈕按下後暫停幾毫秒，避免連點洗版

# 深色主題的顏色，想換配色改這裡就好
BG = "#121212"  # 視窗背景
PANEL = "#1E1E1E"  # 輸入框、訊息區的背景
TEXT = "#EDEDED"  # 一般文字
MUTED = "#9A9A9A"  # 說明文字、系統訊息
ACCENT = "#2F6FED"  # 主要按鈕
ME_BUBBLE = "#8DE08A"  # 自己的訊息（綠色泡泡）
ME_FG = "#10240F"  # 綠色泡泡上的文字
OTHER_BUBBLE = "#2C2C2E"  # 別人的訊息
TOPIC_BG = "#F2B84B"  # 辯論題的底色
TOPIC_FG = "#1F1300"  # 辯論題的文字
ERROR = "#FF6B6B"
BUTTON_BG = "#2C2C2E"
BUTTON_HOVER = "#3A3A3C"
BUTTON_DISABLED_FG = "#6B6B6B"


class DarkButton(tk.Label):
    """用 Label 做的按鈕。

    macOS 上原生的 tk.Button 不能改底色，所以用 Label 加上滑鼠事件來做，
    這樣在 Mac 和 Windows 上看起來才會一樣。
    """

    def __init__(self, parent, text, command, primary=False, width=10):
        self.normal_bg = ACCENT if primary else BUTTON_BG
        self.hover_bg = "#4A83F0" if primary else BUTTON_HOVER
        super().__init__(
            parent,
            text=text,
            width=width,
            bg=self.normal_bg,
            fg="#FFFFFF" if primary else TEXT,
            padx=10,
            pady=7,
            cursor="hand2",
        )
        self.command = command
        self.enabled = True
        self.normal_fg = self.cget("fg")
        self.bind("<Button-1>", self.on_click)
        self.bind("<Enter>", lambda _event: self.enabled and self.config(bg=self.hover_bg))
        self.bind("<Leave>", lambda _event: self.config(bg=self.normal_bg))

    def on_click(self, _event):
        if self.enabled:
            self.command()

    def set_enabled(self, enabled):
        self.enabled = enabled
        self.config(fg=self.normal_fg if enabled else BUTTON_DISABLED_FG, bg=self.normal_bg)


# 對話泡泡的尺寸
MESSAGE_FONT = ("", 14)
SMALL_FONT = ("", 10)
EDGE = 14  # 泡泡離訊息區邊緣的距離（留給小箭頭）
PAD_X = 14  # 泡泡內文字左右的留白
PAD_Y = 9  # 泡泡內文字上下的留白
RADIUS = 16  # 圓角大小
TAIL = 9  # 小箭頭突出的長度
GAP = 12  # 兩則訊息之間的距離


def rounded_points(x1, y1, x2, y2, radius):
    """回傳圓角矩形的座標。搭配 create_polygon(smooth=True) 使用，轉角會被畫成圓弧。"""
    r = min(radius, (x2 - x1) / 2, (y2 - y1) / 2)
    return [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]  # fmt: skip


def dark_entry(parent, **options):
    """深色的輸入框。"""
    return tk.Entry(
        parent,
        bg=PANEL,
        fg=TEXT,
        insertbackground=TEXT,  # 游標顏色
        relief="flat",
        highlightthickness=1,
        highlightbackground="#3A3A3C",
        highlightcolor=ACCENT,
        **options,
    )


# ---------- 網路相關（不碰畫面） ----------


def send_json(sock, obj):
    """把 dict 轉成一行 JSON 送出。結尾的換行是訊息的分隔符號。"""
    sock.sendall((json.dumps(obj) + "\n").encode("utf-8"))


def recv_line(sock):
    """讀到換行為止，回傳一行文字；連線關閉時回傳 None。"""
    buffer = b""
    while True:
        chunk = sock.recv(1)
        if not chunk:
            return None
        if chunk == b"\n":
            break
        buffer += chunk
    return buffer.decode("utf-8", errors="ignore")


def authenticate(host, action, username, password):
    """向 auth server 註冊或登入。

    action 是 "login" 或 "signup"。
    回傳 (是否成功, 要顯示給使用者的訊息)。
    """
    try:
        sock = socket.create_connection((host, AUTH_PORT), timeout=5)
    except OSError as e:
        return False, f"無法連線到驗證伺服器 {host}:{AUTH_PORT}（{e}）"

    try:
        if action == "signup":
            send_json(sock, {"type": "register", "username": username, "password": password})
            response = recv_line(sock)
            if response is None:
                return False, "驗證伺服器中斷了連線"
            if "註冊成功" not in response:
                return False, response

        # 註冊成功後直接登入；一般登入也走這裡
        send_json(sock, {"type": "login", "username": username, "password": password})
        response = recv_line(sock)
        if response is None:
            return False, "驗證伺服器中斷了連線"
        if response == "登入成功":
            return True, response
        return False, response
    except socket.timeout:
        return False, "驗證伺服器沒有回應，請確認帳號是否存在"
    except OSError as e:
        return False, f"連線發生錯誤：{e}"
    finally:
        sock.close()


# ---------- 登入視窗 ----------


class LoginWindow:
    def __init__(self, root):
        self.root = root
        self.root.title("聊天室 - 登入")
        self.root.resizable(False, False)
        self.result_queue = queue.Queue()
        self.root.config(bg=BG)

        frame = tk.Frame(root, padx=28, pady=24, bg=BG)
        frame.pack()

        tk.Label(frame, text="辯論破冰聊天室", font=("", 20, "bold"), bg=BG, fg=TEXT).grid(
            row=0, column=0, columnspan=2, pady=(0, 4)
        )
        tk.Label(
            frame,
            text="第一次使用請輸入想要的帳號密碼，再按「註冊」。",
            bg=BG,
            fg=MUTED,
        ).grid(row=1, column=0, columnspan=2, pady=(0, 14))

        tk.Label(frame, text="伺服器位址", bg=BG, fg=TEXT).grid(
            row=2, column=0, sticky="e", padx=(0, 8), pady=5
        )
        self.host_entry = dark_entry(frame, width=26)
        self.host_entry.insert(0, DEFAULT_HOST)
        self.host_entry.grid(row=2, column=1, pady=5)

        tk.Label(frame, text="帳號", bg=BG, fg=TEXT).grid(
            row=3, column=0, sticky="e", padx=(0, 8), pady=5
        )
        self.username_entry = dark_entry(frame, width=26)
        self.username_entry.grid(row=3, column=1, pady=5)

        tk.Label(frame, text="密碼", bg=BG, fg=TEXT).grid(
            row=4, column=0, sticky="e", padx=(0, 8), pady=5
        )
        self.password_entry = dark_entry(frame, width=26, show="*")
        self.password_entry.grid(row=4, column=1, pady=5)

        buttons = tk.Frame(frame, bg=BG)
        buttons.grid(row=5, column=0, columnspan=2, pady=(16, 6))
        self.login_button = DarkButton(
            buttons, "登入", lambda: self.submit("login"), primary=True
        )
        self.login_button.pack(side="left", padx=6)
        self.signup_button = DarkButton(buttons, "註冊", lambda: self.submit("signup"))
        self.signup_button.pack(side="left", padx=6)

        self.status_label = tk.Label(
            frame, text="", bg=BG, fg=ERROR, wraplength=320, justify="left"
        )
        self.status_label.grid(row=6, column=0, columnspan=2, pady=(6, 0))

        # 在密碼欄按 Enter 等於按「登入」
        self.password_entry.bind("<Return>", lambda _event: self.submit("login"))
        self.username_entry.focus_set()

    def submit(self, action):
        host = self.host_entry.get().strip()
        username = self.username_entry.get().strip()
        password = self.password_entry.get()

        if not host or not username or not password:
            self.show_status("伺服器位址、帳號、密碼都要填。")
            return

        self.set_busy(True)
        self.show_status("連線中…", error=False)

        # 連線可能要等幾秒，放到背景執行緒做，視窗才不會卡住
        def worker():
            ok, message = authenticate(host, action, username, password)
            self.result_queue.put((ok, message, host, username))

        threading.Thread(target=worker, daemon=True).start()
        self.root.after(100, self.check_result)

    def check_result(self):
        try:
            ok, message, host, username = self.result_queue.get_nowait()
        except queue.Empty:
            self.root.after(100, self.check_result)
            return

        self.set_busy(False)
        if not ok:
            self.show_status(message)
            return
        self.open_chat(host, username)

    def open_chat(self, host, username):
        try:
            sock = socket.create_connection((host, CHAT_PORT), timeout=5)
            sock.settimeout(None)  # 連上之後不設逾時，閒置時才不會被當成斷線
        except OSError as e:
            self.show_status(f"登入成功，但無法連線到聊天伺服器 {host}:{CHAT_PORT}（{e}）")
            return

        # 清掉登入畫面，換成聊天畫面
        for widget in self.root.winfo_children():
            widget.destroy()
        ChatWindow(self.root, sock, host, username)

    def set_busy(self, busy):
        self.login_button.set_enabled(not busy)
        self.signup_button.set_enabled(not busy)

    def show_status(self, text, error=True):
        self.status_label.config(text=text, fg=ERROR if error else MUTED)


# ---------- 聊天視窗 ----------


class ChatWindow:
    def __init__(self, root, sock, host, username):
        self.root = root
        self.sock = sock
        self.host = host
        self.username = username
        self.running = True
        self.incoming = queue.Queue()  # 背景執行緒 → 主執行緒
        # 目前正在收哪一種「多行回覆」：
        #   "fetch"   進聊天室時載入的歷史訊息
        #   "history" 查詢某位使用者的歷史訊息
        #   None      一般的即時訊息
        self.mode = "fetch"
        self.history_target = ""
        self.history_lines = []
        self.items = []  # 畫面上所有的訊息，視窗改變大小時要靠它重畫
        self.canvas_width = 0
        self.next_y = GAP  # 下一則訊息要畫在多高的位置

        self.root.title(f"聊天室 - {username}")
        self.root.resizable(True, True)
        self.root.minsize(640, 480)
        self.root.protocol("WM_DELETE_WINDOW", self.leave)

        self.build_widgets()

        # 進聊天室先要歷史訊息，再開始接收即時訊息
        self.add_line("—— 先前的訊息 ——", "system")
        self.send({"type": "fetch"})

        threading.Thread(target=self.receive_loop, daemon=True).start()
        self.root.after(100, self.poll_queue)

    # ----- 畫面 -----

    def build_widgets(self):
        self.root.config(bg=BG)
        outer = tk.Frame(self.root, padx=12, pady=12, bg=BG)
        outer.pack(fill="both", expand=True)

        tk.Label(
            outer,
            text="在下方輸入訊息後連按兩次 Enter，或按「送出」。按「抽辯論題」會隨機出一題給聊天室裡的所有人。",
            bg=BG,
            fg=MUTED,
            anchor="w",
            justify="left",
            wraplength=700,
        ).pack(fill="x", pady=(0, 8))

        body = tk.Frame(outer, bg=BG)
        body.pack(fill="both", expand=True)

        # 右邊：功能按鈕
        side = tk.Frame(body, bg=BG)
        side.pack(side="right", fill="y", padx=(10, 0))

        self.topic_button = DarkButton(side, "抽辯論題", self.draw_topic, primary=True)
        self.topic_button.pack(pady=(0, 8))
        DarkButton(side, "線上名單", self.ask_online).pack(pady=(0, 8))
        DarkButton(side, "歷史訊息", self.ask_history).pack(pady=(0, 8))
        DarkButton(side, "離開", self.leave).pack(side="bottom")

        # 左邊：訊息區。用 Canvas 自己畫對話泡泡（圓角、小箭頭），Text 元件做不到
        area = tk.Frame(body, bg=PANEL)
        area.pack(side="left", fill="both", expand=True)
        scrollbar = tk.Scrollbar(area, orient="vertical")
        scrollbar.pack(side="right", fill="y")
        self.canvas = tk.Canvas(
            area, bg=PANEL, highlightthickness=0, yscrollcommand=scrollbar.set
        )
        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.config(command=self.canvas.yview)
        self.canvas.bind("<Configure>", self.on_resize)
        self.canvas.bind("<MouseWheel>", self.on_mousewheel)  # macOS、Windows
        self.canvas.bind("<Button-4>", lambda _event: self.canvas.yview_scroll(-2, "units"))
        self.canvas.bind("<Button-5>", lambda _event: self.canvas.yview_scroll(2, "units"))

        # 下方：輸入區
        bottom = tk.Frame(outer, bg=BG)
        bottom.pack(fill="x", pady=(10, 0))
        self.entry = dark_entry(bottom)
        self.entry.pack(side="left", fill="x", expand=True, ipady=6)
        # 連按兩次 Enter 才送出：用中文輸入法時，第一次 Enter 通常是用來選字，
        # 如果按一次就送出，字還沒選完訊息就送出去了。
        # 兩次 Enter 之間只要按了別的鍵，就要重新算。
        self.enter_pressed_once = False
        self.entry.bind("<Return>", self.on_enter)
        self.entry.bind("<Key>", self.on_other_key)
        DarkButton(bottom, "送出", self.send_message, primary=True, width=6).pack(
            side="left", padx=(8, 0)
        )
        self.entry.focus_set()

    # ----- 畫訊息 -----

    def on_resize(self, event):
        """視窗寬度改變時全部重畫，靠右的泡泡才會跟著貼齊右邊。"""
        if event.width != self.canvas_width:
            self.canvas_width = event.width
            self.redraw()

    def on_mousewheel(self, event):
        # macOS 的 delta 是小數字，Windows 是 120 的倍數
        steps = -event.delta if abs(event.delta) < 120 else -event.delta // 120
        self.canvas.yview_scroll(int(steps), "units")

    def redraw(self):
        self.canvas.delete("all")
        self.next_y = GAP
        for item in self.items:
            self.draw_item(item)
        self.finish_draw()

    def finish_draw(self):
        self.canvas.config(scrollregion=(0, 0, self.canvas_width, self.next_y + GAP))
        self.canvas.yview_moveto(1.0)  # 捲到最下面

    def add_item(self, kind, content, name="", time=""):
        item = {"kind": kind, "content": content, "name": name, "time": time}
        self.items.append(item)
        if self.canvas_width:  # 視窗還沒顯示時寬度是 0，等 on_resize 再一起畫
            self.draw_item(item)
            self.finish_draw()

    def draw_item(self, item):
        kind = item["kind"]
        if kind == "me":
            self.draw_bubble(item, right=True)
        elif kind == "other":
            self.draw_bubble(item, right=False)
        elif kind == "topic":
            self.draw_topic_card(item["content"])
        else:
            self.draw_system(item["content"])

    def draw_bubble(self, item, right):
        """畫一則對話泡泡。right=True 是自己的（靠右、綠色），False 是別人的（靠左、深灰）。"""
        canvas = self.canvas
        width = self.canvas_width
        max_text_width = max(160, int(width * 0.6))  # 文字最寬佔訊息區的六成，超過就換行
        y = self.next_y

        # 別人的訊息上方先寫名字
        if not right and item["name"]:
            name_id = canvas.create_text(
                EDGE + TAIL + 4, y, anchor="nw", text=item["name"], fill=MUTED, font=SMALL_FONT
            )
            y = canvas.bbox(name_id)[3] + 3

        # 1. 先放文字，才知道泡泡要多大
        if right:
            text_id = canvas.create_text(
                width - EDGE - TAIL - PAD_X, y + PAD_Y, anchor="ne", justify="left",
                text=item["content"], width=max_text_width, fill=ME_FG, font=MESSAGE_FONT,
            )  # fmt: skip
        else:
            text_id = canvas.create_text(
                EDGE + TAIL + PAD_X, y + PAD_Y, anchor="nw", justify="left",
                text=item["content"], width=max_text_width, fill=TEXT, font=MESSAGE_FONT,
            )  # fmt: skip
        x1, y1, x2, y2 = canvas.bbox(text_id)
        bx1, by1, bx2, by2 = x1 - PAD_X, y1 - PAD_Y, x2 + PAD_X, y2 + PAD_Y
        color = ME_BUBBLE if right else OTHER_BUBBLE

        # 2. 畫圓角泡泡
        canvas.create_polygon(
            rounded_points(bx1, by1, bx2, by2, RADIUS), smooth=True, fill=color, outline=""
        )

        # 3. 畫小箭頭：泡泡上緣往外突出的小三角形
        if right:
            tail = [bx2 - 12, by1 + 3, bx2 + TAIL, by1 + 1, bx2 - 2, by1 + 17]
        else:
            tail = [bx1 + 12, by1 + 3, bx1 - TAIL, by1 + 1, bx1 + 2, by1 + 17]
        canvas.create_polygon(tail, fill=color, outline="")

        # 4. 把文字移到泡泡上面（後畫的東西會蓋住先畫的）
        canvas.tag_raise(text_id)

        # 5. 時間寫在泡泡外側、靠下
        if item["time"]:
            if right:
                canvas.create_text(
                    bx1 - 8, by2, anchor="se", text=item["time"], fill=MUTED, font=SMALL_FONT
                )
            else:
                canvas.create_text(
                    bx2 + 8, by2, anchor="sw", text=item["time"], fill=MUTED, font=SMALL_FONT
                )

        self.next_y = by2 + GAP

    def draw_topic_card(self, content):
        """辯論題：置中的琥珀色卡片。"""
        canvas = self.canvas
        width = self.canvas_width
        text_id = canvas.create_text(
            width / 2, self.next_y + PAD_Y + 4, anchor="n", justify="center",
            text="辯論題\n" + content, width=int(width * 0.75), fill=TOPIC_FG,
            font=("", 15, "bold"),
        )  # fmt: skip
        x1, y1, x2, y2 = canvas.bbox(text_id)
        canvas.create_polygon(
            rounded_points(x1 - 20, y1 - 12, x2 + 20, y2 + 12, RADIUS),
            smooth=True, fill=TOPIC_BG, outline="",
        )  # fmt: skip
        canvas.tag_raise(text_id)
        self.next_y = y2 + 12 + GAP + 4

    def draw_system(self, content):
        """系統訊息：置中的灰色小字。"""
        text_id = self.canvas.create_text(
            self.canvas_width / 2, self.next_y, anchor="n", justify="center",
            text=content, width=int(self.canvas_width * 0.85), fill=MUTED, font=("", 11),
        )  # fmt: skip
        self.next_y = self.canvas.bbox(text_id)[3] + GAP

    def add_line(self, line, tag=None, live=True):
        """把伺服器送來的一行文字變成畫面上的一則訊息。

        聊天訊息的格式是「名字: 內容」。
        名字是自己 → 靠右的綠色泡泡；是別人 → 靠左的泡泡並顯示名字；
        辯論題與系統訊息 → 置中。
        live=False 代表是歷史訊息：伺服器沒有回傳時間，所以不顯示時間。
        """
        if tag == "system":
            self.add_item("system", line)
            return

        if line.startswith(TOPIC_PREFIX):
            self.add_item("topic", line[len(TOPIC_PREFIX):])
            return

        if line.startswith(ONLINE_PREFIX) or line.startswith("[系統]"):
            self.add_item("system", line)
            return

        name, separator, content = line.partition(": ")
        if not separator:
            self.add_item("system", line)
            return

        time = datetime.now().strftime("%H:%M") if live else ""
        if name == self.username:
            self.add_item("me", content, time=time)
        else:
            self.add_item("other", content, name=name, time=time)

    # ----- 送出 -----

    def send(self, obj):
        """補上每則訊息都要帶的欄位後送給 chat server。"""
        message = {
            "username": self.username,
            # 伺服器用 ip + port 當作「聊天室」的名稱，和終端機版 client 相同
            "ip": self.host,
            "port": str(CHAT_PORT),
        }
        message.update(obj)
        try:
            send_json(self.sock, message)
            return True
        except OSError:
            self.connection_lost()
            return False

    def on_enter(self, _event):
        if self.enter_pressed_once:
            self.send_message()
        else:
            self.enter_pressed_once = True
        return "break"  # 這個按鍵處理完了，不要再交給 on_other_key

    def on_other_key(self, _event):
        self.enter_pressed_once = False

    def send_message(self):
        self.enter_pressed_once = False
        text = self.entry.get().strip()
        if not text:
            return
        if self.send({"type": "message", "message": text}):
            # 伺服器不會把訊息傳回給發送者，所以自己先顯示
            self.add_line(f"{self.username}: {text}")
            self.entry.delete(0, "end")

    def draw_topic(self):
        if self.send({"type": "topic"}):
            # 題目由伺服器廣播回來（包含自己），所以這裡不用自己顯示
            self.topic_button.set_enabled(False)
            self.root.after(TOPIC_COOLDOWN_MS, lambda: self.topic_button.set_enabled(True))

    def ask_online(self):
        self.send({"type": "online"})

    def ask_history(self):
        if self.mode is not None:
            return  # 上一個查詢還沒收完
        target = simpledialog.askstring(
            "歷史訊息", "要查詢哪一個帳號說過的話？", initialvalue=self.username, parent=self.root
        )
        if not target or not target.strip():
            return
        self.history_target = target.strip()
        self.history_lines = []
        self.mode = "history"
        if not self.send({"type": "history", "username": self.history_target}):
            self.mode = None

    # ----- 接收 -----

    def receive_loop(self):
        """背景執行緒：只負責收，收到的每一行都丟進 queue。"""
        try:
            while self.running:
                line = recv_line(self.sock)
                if line is None:
                    break
                self.incoming.put(line)
        except OSError:
            pass
        self.incoming.put(None)  # None 代表連線結束

    def poll_queue(self):
        """主執行緒：定時把 queue 裡的訊息拿出來更新畫面。"""
        try:
            while True:
                line = self.incoming.get_nowait()
                if line is None:
                    if self.running:
                        self.connection_lost()
                    return
                self.handle_line(line)
        except queue.Empty:
            pass
        if self.running:
            self.root.after(100, self.poll_queue)

    def handle_line(self, line):
        if line == "END":
            if self.mode == "fetch":
                self.add_line("—— 以上是先前的訊息 ——", "system")
            elif self.mode == "history":
                self.show_history()
            self.mode = None
            return

        if line == "NO_MESSAGE":
            if self.mode == "fetch":
                self.add_line("（這個聊天室還沒有訊息）", "system")
            return

        if self.mode == "history":
            self.history_lines.append(line)
            return

        # 還在載入歷史訊息時（fetch），這些訊息不是現在才發生的
        self.add_line(line, live=self.mode != "fetch")

    def show_history(self):
        window = tk.Toplevel(self.root)
        window.title(f"{self.history_target} 的歷史訊息")
        window.config(bg=BG)
        text = scrolledtext.ScrolledText(
            window, wrap="word", width=60, height=20, bg=PANEL, fg=TEXT, relief="flat", padx=10, pady=10
        )
        text.pack(fill="both", expand=True, padx=10, pady=10)
        if self.history_lines:
            text.insert("end", "\n".join(self.history_lines))
        else:
            text.insert("end", "查無歷史訊息")
        text.config(state="disabled")

    # ----- 結束 -----

    def connection_lost(self):
        if not self.running:
            return
        self.running = False
        messagebox.showerror("連線中斷", "與聊天伺服器的連線中斷了，請重新開啟程式。", parent=self.root)
        self.close()

    def leave(self):
        self.running = False
        self.close()

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass
        self.root.destroy()


def main():
    root = tk.Tk()
    LoginWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
