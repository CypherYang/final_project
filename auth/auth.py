#功能：客戶端可以註冊帳號和登入，並且能同時服務多個客戶端連線。
import socket
import threading
import json
import os
import pymysql#Python 連接 MySQL 的驅動套件
from dotenv import load_dotenv

load_dotenv()#讀取同目錄下的 .env

PORT = 8080
GREEN = "\033[32m"
RED = "\033[31m"
RESET = "\033[0m"

#資料庫連線函式
def get_db_connection():
    return pymysql.connect(
        #host/user/password/database 全部來自環境變數
        host=os.getenv("DATABASE_HOST"),
        user=os.getenv("DATABASE_USER"),
        password=os.getenv("DATABASE_PASSWORD"),
        database=os.getenv("DATABASE_NAME"),
        autocommit=True,#每次 SQL 執行後自動提交 不用手動執行
    )

#建表函式 init_tables
def init_tables(conn):
    #cur.execute()SQL指令送到db執行 執行結果要由cur.fetchone 來拿
    with conn.cursor() as cur:#conn.cursor()：從資料庫連線（connection）建立一個游標物件，用來執行 SQL 指令
        cur.execute("SHOW TABLES LIKE 'users';")#LIKE 'users'：篩選出名稱符合 'users' 的資料表
        if cur.fetchone() is None:#若不存在就建立 users 表
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
        if cur.fetchone() is None:#檢查並建立 messages 表
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

#使用者查詢/新增輔助函式
def username_exists(conn, username):
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM users WHERE username = %s;", (username,))
        (count,) = cur.fetchone()#解構賦值，把查詢結果的第一欄取出成 count
        return count > 0

#依 username 查出資料庫中儲存的密碼字串
def get_stored_password(conn, username):
    with conn.cursor() as cur:
        cur.execute("SELECT password FROM users WHERE username = %s;", (username,))
        row = cur.fetchone()
        return row[0] if row else ""

#新使用者的帳號密碼寫入資料庫
def insert_user(conn, username, password):
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (username, password) VALUES (%s, %s);",
            (username, password),
        )
    return True

#傳送資料給客戶端
def send_line(conn_socket, text):
    conn_socket.sendall((text + "\n").encode("utf-8"))

#處理每個客戶端連線
def handle_client(client_socket, addr):
    print(f"New connection from {addr}")#函式會在獨立執行緒中執行
    db_conn = get_db_connection()#在main裡面有thread建立一條資料庫連線
    buffer = ""
    try:
        while True:
            data = client_socket.recv(4096)#最多讀 4096 bytes
            if not data:#對方已經關閉連線
                break
            #先把資料收下來再處理,避免TCP沒傳完就開始處理
            buffer += data.decode("utf-8", errors="ignore")#收到的 bytes 解碼成字串（遇到無法解碼的字元直接忽略），累加進 buffer
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)#只要 buffer 裡還有換行符號，就代表至少有一行完整訊息
                if not line.strip():
                    continue
                try:
                    message = json.loads(line)#JSON=>dict
                except json.JSONDecodeError:
                    print(f"Invalid JSON received: {line}")
                    continue

                msg_type = message.get("type")#訊息的 "type" 欄位，用來決定要做什麼動作（register / login / message）

                if msg_type == "register":
                    username = message.get("username", "")
                    password = message.get("password", "")
                    if username_exists(db_conn, username):
                        send_line(client_socket, "使用者已存在，請重新註冊")
                    else:
                        if insert_user(db_conn, username, password):
                            send_line(client_socket, f"使用者 {username} 註冊成功!")
                        else:
                            print("Error inserting user into database")

                elif msg_type == "login":
                    username = message.get("username", "")
                    password = message.get("password", "")
                    stored_password = get_stored_password(db_conn, username)
                    if not stored_password:
                        print("Error retrieving stored password")
                        continue
                    if stored_password == password:
                        send_line(client_socket, "登入成功")
                    else:
                        send_line(client_socket, "帳號或密碼錯誤!")

                elif msg_type == "message":
                    print("message received")

    except Exception as e:
        print(f"Exception in thread: {e}")
    finally:
        client_socket.close()
        db_conn.close()
        print(f"Connection closed: {addr}")


def main():
    init_conn = get_db_connection()
    print(f"{GREEN}Connected to the MySQL server successfully!{RESET}")
    init_tables(init_conn)
    init_conn.close()

    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)#建立一個 TCP 參數(IPv4 , TCP)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)#設定 socket 參數(socket 這一層,允許重複使用位址)
    server_socket.bind(("0.0.0.0", PORT))#socket 綁定(ip,特定port)
    server_socket.listen()#開始監聽

    print(f"{GREEN}Auth server is listening on port {PORT}{RESET}")

    try:
        while True:
            client_socket, addr = server_socket.accept()
            thread = threading.Thread(#daemon 執行緒去執行 handle_client
                target=handle_client,#是把「函式本身」當成一個物件傳進去
                args=(client_socket, addr),
                daemon=True,#主程式（main()）結束時，這條執行緒會被直接強制砍掉
            )# 等同於執行緒啟動後，會呼叫：handle_client(client_socket, addr)
            thread.start()
    except KeyboardInterrupt:
        print("Shutting down auth server...")
    finally:
        server_socket.close()


if __name__ == "__main__":
    main()
