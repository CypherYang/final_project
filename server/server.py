import socket
import threading
import json
import os
import traceback
import pymysql
from dotenv import load_dotenv

load_dotenv()

PORT = 8080
GREEN = "\033[32m"
RED = "\033[31m"
RESET = "\033[0m"
YELLOW = "\033[33m"

clients_lock = threading.Lock()
clients = set()  # set of (socket, room_key) tuples, room_key = "ip:port"


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


def send_line(sock, text):
    try:
        sock.sendall((text + "\n").encode("utf-8"))
    except OSError:
        pass


def room_key(ip, port):
    return f"{ip}:{port}"


def insert_message(conn, username, message, room):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO messages (username, message, ip) VALUES (%s, %s, %s);",
            (username, message, room),
        )


def fetch_room_messages(conn, room):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT username, message FROM messages WHERE ip = %s ORDER BY id ASC;",
            (room,),
        )
        return cur.fetchall()


def fetch_user_messages(conn, username, room):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT username, message FROM messages WHERE ip = %s AND username = %s ORDER BY id ASC;",
            (room, username),
        )
        return cur.fetchall()


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

                    with clients_lock:
                        clients.add((client_socket, room))
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
                    with clients_lock:
                        clients.add((client_socket, room))
                    current_room = room

                    rows = fetch_room_messages(db_conn, room)
                    if not rows:
                        send_line(client_socket, "NO_MESSAGE")
                    for uname, msg in rows:
                        send_line(client_socket, f"{uname}: {msg}")
                    send_line(client_socket, "END")

                elif msg_type == "history":
                    rows = fetch_user_messages(db_conn, username, room)
                    if not rows:
                        send_line(client_socket, "NO_MESSAGE")
                    for uname, msg in rows:
                        send_line(client_socket, f"{uname}: {msg}")
                    send_line(client_socket, "END")

                elif msg_type == "online":
                    with clients_lock:
                        clients.add((client_socket, room))
                    current_room = room
                    print(f"{username} is online in room {room}")

    except Exception as e:
        print(f"{RED}Exception in thread: {e}{RESET}")
        traceback.print_exc()
    finally:
        with clients_lock:
            if current_room is not None:
                clients.discard((client_socket, current_room))
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
