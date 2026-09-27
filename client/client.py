import socket#socket 模組，提供建立 TCP/UDP 網路連線的功能。這裡用來建立跟伺服器之間的 TCP 連線。
import threading#同時「監聽伺服器傳來的訊息」跟「等待使用者打字輸入」
import json#json.dumps() dict=>字串(用來經由網路送訊息) json.loads 則是相反
import sys
import os

GREEN = "\033[32m"
RED = "\033[31m"
RESET = "\033[0m"
YELLOW = "\033[33m"

# 在 Docker 環境下可直接用容器名稱 "auth"，不需要查 IP
AUTH_HOST = os.getenv("AUTH_HOST", "auth")
AUTH_PORT = 8080
#它們是不同容器、有各自獨立的網路命名空間，8080 只是「容器內部」在聽的埠，不會互相衝突。
CHAT_PORT = 8080

running = True
account = {"username": "", "password": ""}


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")
    #windows(nt)用的是cls macos(posix)用的是clear

def print_banner():
    print(
        r"""
 __          __    _                                 _____  _             _     _____
 \ \        / /   | |                               / ____|| |           | |   / ____|
  \ \  /\  / /___ | |  ___  ___   _ __ ___    ___   | |     | |__    __ _ | |_ | (___
   \ \/  \/ // _ \| | / __|/ _ \ | '_ ` _ \  / _ \  | |     | '_ \  / _` || __| \___ \
    \  /\  /|  __/| || (__| (_) || | | | | ||  __/  | |____ | | | || (_| || |_  ____) |
     \/  \/  \___||_| \___|\___/ |_| |_| |_| \___|   \_____||_| |_| \__,_| \__||_____/
"""
    )


def send_json(sock, obj):#sock（已建立好的 socket 連線物件)
    sock.sendall((json.dumps(obj) + "\n").encode("utf-8"))
    #因為 TCP 是串流協定，資料可能會黏在一起或被拆開，需要一個明確的分隔符
    #字串轉成 bytes（位元組），因為網路傳輸只能傳 bytes，不能直接傳字串。
    #把這些位元組完整送出去（sendall 會確保全部資料都送完
    
#接收一行訊息
def recv_line(sock):
    buffer = b""
    while True:
        chunk = sock.recv(1)#從 socket 讀取剛好 1 個位元組
        if not chunk:#如果 chunk 是空的 bytes
            return None
        if chunk == b"\n":
            break
        buffer += chunk#不是換行符號，就把這個位元組接到 buffer 後面，繼續讀下一個位元組。
    return buffer.decode("utf-8", errors="ignore")#收集到的 bytes 解碼成字串（用 UTF-8 編碼

#處理註冊
def handle_registration(sock):
    username = input(f"{GREEN}請輸入使用者名稱：{RESET}")
    password = input(f"{GREEN}請輸入密碼：{RESET}")
    #寫入全域字典 account
    account["username"] = username
    account["password"] = password
    send_json(sock, {"type": "register", "username": username, "password": password})
    #字典（含有 type 欄位標明是 "register" 註冊請求，以及帳密）送給伺服器。
    response = recv_line(sock)#等待伺服器回應一行文字
    print(f"{RED}{response}{RESET}")
    return response != "使用者已存在，請重新註冊"

#處理登入
def handle_login(sock):
    if not account["username"]:#如果目前還沒有帳號名稱
        username = input(f"{GREEN}請輸入使用者名稱：{RESET}")
        password = input(f"{GREEN}請輸入密碼：{RESET}")
        account["username"] = username
        account["password"] = password
    send_json(
        sock,
        {"type": "login", "username": account["username"], "password": account["password"]},
    )
    response = recv_line(sock)
    if response == "登入成功":
        print(f"{YELLOW}登入成功，歡迎 {account['username']}!{RESET}")
        return True
    else:
        print(f"{RED}登入失敗: {response}{RESET}")
        return False

#驗證流程主控函式
def auth_flow():
    try:
        sock = socket.create_connection((AUTH_HOST, AUTH_PORT), timeout=5)#例外處理區塊，因為連線可能失敗
    except OSError as e:
        print(f"{RED}無法連線至驗證伺服器 ({AUTH_HOST}:{AUTH_PORT}): {e}{RESET}")
        return None

    logged_in = False#一直讓使用者嘗試，直到成功登入為止
    while not logged_in:
        choice = input(f"{GREEN}請選擇登入(login)或是註冊(signup)\n輸入:{RESET}")
        if choice == "login":
            logged_in = handle_login(sock)
        elif choice == "signup":
            if handle_registration(sock):
                logged_in = handle_login(sock)
        else:
            print(f"{RED}輸入錯誤，請重新輸入{RESET}")

    sock.close()#logged_in 已經是 True 後，關閉這條連線
    return True

#背景執行緒：接收訊息
def receive_messages(sock):
    global running
    try:
        while running:
            line = recv_line(sock)
            if line is None:
                break
            if line in ("END", "NO_MESSAGE"):
                continue
            print(f"\r\033[2K{YELLOW}{line}{RESET}", flush=True)
            print(f"\033[7m<{account['username']}> {RESET}", end="", flush=True)
    except OSError as e:
        if running:
            print(f"{RED}接收訊息時連線中斷: {e}{RESET}")

#顯示說明
def print_help():
    print("可用的命令:\n  !history       查看歷史訊息\n  !exit          離開\n  !help          顯示說明")

#查詢特定使用者的歷史訊息
def request_history(sock, server_host, server_port, target_username):
    send_json(
        sock,
        {
            "type": "history",
            "username": target_username,
            "ip": server_host,
            "port": str(server_port),
        },
    )
    print(f"使用者 '{target_username}' 的歷史訊息：")
    while True:
        line = recv_line(sock)
        if line is None or line == "END":
            break
        if line == "NO_MESSAGE":
            print("查無歷史訊息")
            continue
        print(f"\033[41m{line}{RESET}")

#抓取目前聊天室的歷史訊息
def fetch_room_history(sock, server_host, server_port):
    send_json(
        sock,
        {
            "type": "fetch",
            "username": account["username"],
            "ip": server_host,
            "port": str(server_port),
        },
    )
    while True:
        line = recv_line(sock)
        if line is None or line == "END":
            break
        if line == "NO_MESSAGE":
            continue
        print(f"{YELLOW}{line}{RESET}")

#傳送訊息 / 處理指令的主迴圈
def send_messages(sock, server_host, server_port):
    global running
    username = account["username"]
    while running:
        try:
            message = input(f"\033[7m<{username}> {RESET}")
        except EOFError:
            running = False
            break
        if not message.strip():
            continue

        if message == "!exit":
            running = False
            sock.close()
            break
        elif message == "!history":
            target = input("請輸入要查詢的帳號：")
            request_history(sock, server_host, server_port, target)
        elif message == "!help":
            print_help()
        elif message == "!online":
            send_json(
                sock,
                {
                    "type": "online",
                    "username": username,
                    "ip": server_host,
                    "port": str(server_port),
                },
            )
        else:
            #如果輸入的內容不符合上面任何一個 ! 指令，就當作一般聊天訊息處理：
            print(f"{YELLOW}{username}: {message}{RESET}")
            send_json(
                sock,
                {
                    "type": "message",
                    "username": username,
                    "message": message,
                    "ip": server_host,
                    "port": str(server_port),
                },
            )


def main():
    global running
    print_banner()

    if not auth_flow():
        sys.exit(1)#回傳結束碼 1（代表異常結束）

    while True:
        server_host = input(f"{GREEN}請輸入伺服器 IP 位址（Docker 環境下可直接輸入 server）：{RESET}")
        if server_host == "exit":
            return

        try:
            sock = socket.create_connection((server_host, CHAT_PORT), timeout=5)
            sock.settimeout(None)  # 連線建立後解除逾時，避免閒置時被誤判斷線
        except OSError as e:
            print(f"{RED}連線逾時或失敗：{e}{RESET}")
            continue

        clear_screen()
        running = True
        print(f"{GREEN}已連線至伺服器 {server_host}{RESET}")
        fetch_room_history(sock, server_host, CHAT_PORT)

        recv_thread = threading.Thread(target=receive_messages, args=(sock,), daemon=True)
        '''
        threading.Thread(target=函式, args=(參數,), daemon=True)：建立一個新的執行緒物件，指定它要執行的目標函式是 receive_messages，並把 sock 當作參數傳進去。daemon=True 代表這是一個「守護執行緒」——當主程式（主執行緒）結束時，這個執行緒會被強制終止，不會讓程式卡住無法退出。
        '''
        recv_thread.start()#啟動這個執行緒

        send_messages(sock, server_host, CHAT_PORT)#send_messages，這是一個會持續問使用者

        recv_thread.join(timeout=1)#等待背景執行緒 recv_thread 結束
        clear_screen()
        break


if __name__ == "__main__":
    main()
