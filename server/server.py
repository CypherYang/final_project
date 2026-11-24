import socket
import threading
import json
import os
import traceback
import pymysql
from dotenv import load_dotenv

from topics import DEFAULT_TOPICS  # 預設的辯論題庫

load_dotenv()

PORT = 8080
GREEN = "\033[32m"
RED = "\033[31m"
RESET = "\033[0m"
YELLOW = "\033[33m"

clients_lock = threading.Lock()
clients = set()  # set of (socket, room_key) tuples, room_key = "ip:port"
usernames = {}  # socket -> username，用來回答「線上名單」（同樣由 clients_lock 保護）

TOPIC_SENDER = "辯論題"  # 辯論題存進 messages 時使用的名稱

#資料庫層


def get_db_connection():
    return pymysql.connect(
        host=os.getenv("DATABASE_HOST"),
        user=os.getenv("DATABASE_USER"),
        password=os.getenv("DATABASE_PASSWORD"),
        database=os.getenv("DATABASE_NAME"),
        autocommit=True,
        connect_timeout=5,
        read_timeout=5,
        write_timeout=5,
    )

#多建第三張 debate_topics
def init_tables(conn):
    with conn.cursor() as cur:
        cur.execute("SHOW TABLES LIKE 'users';")
        if cur.fetchone() is None:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INT PRIMARY KEY AUTO_INCREMENT,
                    username VARCHAR(255),
                    password VARCHAR(255)
                );
                """
            )
            print(f"{GREEN}Users table created successfully!{RESET}")
        else:
            print(f"{GREEN}Users table already exists!{RESET}")

        cur.execute("SHOW TABLES LIKE 'messages';")
        if cur.fetchone() is None:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INT PRIMARY KEY AUTO_INCREMENT,
                    username VARCHAR(255),
                    message TEXT,
                    ip VARCHAR(45),
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            print(f"{GREEN}Messages table created successfully!{RESET}")
        else:
            print(f"{GREEN}Messages table already exists!{RESET}")

        # 辯論題庫：資料表不存在就建立，裡面沒有題目就匯入預設題目
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS debate_topics (
                id INT PRIMARY KEY AUTO_INCREMENT,
                content VARCHAR(255) NOT NULL UNIQUE
            );
            """
        )
        cur.execute("SELECT COUNT(*) FROM debate_topics;")
        (topic_count,) = cur.fetchone()
        if topic_count == 0:
            cur.executemany(
                "INSERT INTO debate_topics (content) VALUES (%s);",
                [(topic,) for topic in DEFAULT_TOPICS],
            )
            print(f"{GREEN}Imported {len(DEFAULT_TOPICS)} debate topics!{RESET}")
        else:
            print(f"{GREEN}Debate topics table has {topic_count} topics.{RESET}")

#工具 / 主流程


#送文字給client 
def send_line(sock, text):
    try:
        sock.sendall((text + "\n").encode("utf-8"))
    except OSError:
        pass

#把 ip 和 port 組成 "ip:port" 字串當房號
def room_key(ip, port):
    return f"{ip}:{port}"

#寫一則訊息進 messages
def insert_message(conn, username, message, room):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO messages (username, message, ip) VALUES (%s, %s, %s);",
            (username, message, room),
        )

#撈整間房的所有訊息
def fetch_room_messages(conn, room):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT username, message FROM messages WHERE ip = %s ORDER BY id ASC;",
            (room,),
        )
        return cur.fetchall()

#撈某個帳號在某間房說過的所有話
def fetch_user_messages(conn, username, room):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT username, message FROM messages WHERE ip = %s AND username = %s ORDER BY id ASC;",
            (room, username),
        )
        return cur.fetchall()

#ORDER BY RAND() LIMIT 1 隨機抽一題。題庫空回 None
def fetch_random_topic(conn):
    """從題庫隨機取一題；題庫是空的時候回傳 None。"""
    with conn.cursor() as cur:
        cur.execute("SELECT content FROM debate_topics ORDER BY RAND() LIMIT 1;")
        row = cur.fetchone()
        return row[0] if row else None



#連線狀態層
#回傳目前在這間房的使用者名單，去重後排序

def online_usernames(room):
    """回傳目前在這個聊天室裡的使用者名稱（去除重複並排序）。"""
    with clients_lock:
        names = {usernames.get(sock, "") for sock, sock_room in clients if sock_room == room}
    return sorted(name for name in names if name)

#把這條連線登記進 clients 集合，使用者名稱記進 usernames 字典
def remember_client(client_socket, room, username):
    """把連線記到聊天室裡，並記下它的使用者名稱。"""
    with clients_lock:
        clients.add((client_socket, room))
        if username:
            usernames[client_socket] = username

#把一行文字送給同一間房的所有人
def broadcast(room, text, exclude_socket=None):
    with clients_lock:
        dead = []
        for sock, sock_room in list(clients):
            if sock_room != room:
                continue
            if sock is exclude_socket:
                continue
            try:
                send_line(sock, text)
            except OSError:
                dead.append((sock, sock_room))
        for item in dead:
            clients.discard(item)



#收訊息 切行 解JSON 分成五種type
def handle_client(client_socket, addr):
    print(f"New connection from {addr}")
    db_conn = get_db_connection()
    buffer = ""
    current_room = None
    try:
        while True:
            data = client_socket.recv(4096)
            if not data:
                break
            buffer += data.decode("utf-8", errors="ignore")

            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                if not line.strip():
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    print(f"Invalid JSON received: {line}")
                    continue

                msg_type = message.get("type")
                username = message.get("username", "")
                ip = message.get("ip", "")
                port = message.get("port", "")
                room = room_key(ip, port)

                if msg_type == "message":
                    text = message.get("message", "")

                    remember_client(client_socket, room, username)
                    current_room = room

                    # 先廣播，確保即時聊天不受資料庫寫入影響
                    broadcast(room, f"{username}: {text}", exclude_socket=client_socket)

                    print(f"{YELLOW}[{room}] {username}: {text}{RESET}")

                    try:
                        insert_message(db_conn, username, text, room)
                    except Exception as db_err:
                        print(f"{RED}DB insert failed: {db_err}{RESET}")
                        try:
                            db_conn.close()
                        except Exception:
                            pass
                        db_conn = get_db_connection()

                elif msg_type == "fetch":
                    remember_client(client_socket, room, username)
                    current_room = room

                    rows = fetch_room_messages(db_conn, room)
                    # 整份回覆一次送出，避免中途被其他人的廣播訊息插進來
                    lines = [f"{uname}: {msg}" for uname, msg in rows] or ["NO_MESSAGE"]
                    send_line(client_socket, "\n".join(lines + ["END"]))

                elif msg_type == "history":
                    rows = fetch_user_messages(db_conn, username, room)
                    lines = [f"{uname}: {msg}" for uname, msg in rows] or ["NO_MESSAGE"]
                    send_line(client_socket, "\n".join(lines + ["END"]))

                elif msg_type == "online":
                    remember_client(client_socket, room, username)
                    current_room = room
                    print(f"{username} is online in room {room}")
                    # 回覆目前在同一個聊天室的使用者名單
                    names = online_usernames(room)
                    send_line(client_socket, "[線上] " + "、".join(names))

                elif msg_type == "topic":
                    remember_client(client_socket, room, username)
                    current_room = room

                    topic = fetch_random_topic(db_conn)
                    if topic is None:
                        send_line(client_socket, "[系統] 題庫目前沒有題目")
                        continue

                    # 題目要讓聊天室裡的每個人都看到，包含按下抽題的人，所以不排除任何連線
                    broadcast(room, f"{TOPIC_SENDER}: {topic}")
                    print(f"{YELLOW}[{room}] {username} drew a topic: {topic}{RESET}")

                    # 存進 messages，之後進聊天室的人在歷史訊息裡也看得到
                    try:
                        insert_message(db_conn, TOPIC_SENDER, topic, room)
                    except Exception as db_err:
                        print(f"{RED}DB insert failed: {db_err}{RESET}")

    except Exception as e:
        print(f"{RED}Exception in thread: {e}{RESET}")
        traceback.print_exc()
    finally:
        with clients_lock:
            if current_room is not None:
                clients.discard((client_socket, current_room))
            usernames.pop(client_socket, None)
        client_socket.close()
        db_conn.close()
        print(f"Connection closed: {addr}")


def main():
    init_conn = get_db_connection()
    print(f"{GREEN}Connected to the MySQL server successfully!{RESET}")
    init_tables(init_conn)
    init_conn.close()

    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind(("0.0.0.0", PORT))
    server_socket.listen()

    print(f"{GREEN}Chat server is listening on port {PORT}{RESET}")

    try:
        while True:
            client_socket, addr = server_socket.accept()
            thread = threading.Thread(
                target=handle_client,
                args=(client_socket, addr),
                daemon=True,
            )
            thread.start()
    except KeyboardInterrupt:
        print("Shutting down chat server...")
    finally:
        server_socket.close()


if __name__ == "__main__":
    main()
