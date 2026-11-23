# Docker Chatroom

用 Python socket 寫的多人聊天室。聊天室裡有一個「抽辯論題」按鈕，按下去會隨機出一題給所有人，讓剛認識的人從辯論中了解彼此。

有**網頁版**和**桌面圖形版**兩種用法。所有服務用 Docker 一個指令啟動，網頁版用瀏覽器就能進入，手機也可以。

## 快速開始

1. 安裝並打開 [Docker Desktop](https://www.docker.com/products/docker-desktop/)，等它顯示 running。
2. 在專案資料夾執行：

   ```bash
   docker compose up -d --build
   ```

3. 瀏覽器打開 **http://localhost:8082**
4. 輸入想要的帳號密碼，按「**註冊**」，就會進入聊天室。

第一次啟動要等資料庫初始化，大約一分鐘。打不開的話等一下再重新整理。

## 功能

| 功能 | 說明 |
|------|------|
| 註冊 / 登入 | 帳號密碼存在 MySQL，擋重複帳號 |
| 即時聊天 | 訊息立刻送到同聊天室的所有人 |
| 抽辯論題 | 從題庫隨機抽一題，聊天室裡所有人同時看到同一題 |
| 線上名單 | 查看目前在聊天室的人 |
| 歷史訊息 | 進聊天室自動載入先前的對話 |
| 跨裝置連線 | 同一個網路的其他電腦、手機都能加入 |

## 網頁版

### 開啟

| 在哪裡開 | 網址 |
|------|------|
| 跑 Docker 的這台電腦 | http://localhost:8082 |
| 同一個網路的其他電腦或手機 | `http://<這台電腦的 IP>:8082` |

查這台電腦的 IP：

```bash
ipconfig getifaddr en0        # macOS
ipconfig                      # Windows，看「IPv4 位址」
```

會得到類似 `192.168.1.10` 的數字，其他裝置就打開 http://192.168.1.10:8082 。

網頁版不用輸入伺服器位址：網址列打的就是伺服器位址，網頁會自動連回同一台電腦。

### 操作

| 動作 | 說明 |
|------|------|
| 送出訊息 | 在下方輸入框按 Enter，或按「送出」。用中文輸入法選字時按的 Enter 不會誤送 |
| 抽辯論題 | 隨機出一題，所有人都會看到黃色的題目卡片。按過後三秒內不能再按 |
| 線上名單 | 顯示目前在聊天室的帳號 |
| 離開 | 登出並回到登入畫面 |

自己的訊息靠右（綠色），別人的訊息靠左並顯示帳號。

帳號不能包含空白或冒號，最多 20 個字。

## 桌面圖形版

不想用瀏覽器的話，也可以用 Python 的 Tkinter 介面。網頁版和桌面圖形版的帳號是共用的。

### 1. 找出能用的 Python

同一台電腦常常裝了好幾個 Python，不是每一個都能開圖形視窗。執行：

```bash
for p in python3 python3.13 python3.12 python3.11; do
  command -v "$p" >/dev/null && printf '%s -> ' "$p" && "$p" -c 'import tkinter; tkinter.Tk().destroy(); print("OK")' 2>&1 | tail -1
done
```

顯示 **OK** 的那一個就能用，下面指令裡的 `python3.13` 換成它。

> 開發機實測：macOS 15.6 上，Apple 內建的 `python3` 會出現 `macOS 15 (1507) or later required` 而無法開啟；Homebrew 的 `python3.13`（執行過 `brew install python-tk`）可以正常使用。

### 2. 開啟

```bash
python3.13 client/gui_client.py
```

登入視窗的「伺服器位址」：在跑 Docker 的電腦上填 `localhost`；其他電腦填那台電腦的 IP。其他電腦只需要 `client/gui_client.py` 這一個檔案。

送出訊息要在輸入框連按兩次 Enter，或按「送出」。

### 重要：所有人填的伺服器位址要一樣

聊天伺服器是用「填的伺服器位址」來區分聊天室的。A 填 `localhost`、B 填 `192.168.1.10`，兩個人雖然連到同一台伺服器，卻會在不同的聊天室，互相看不到訊息。

多台電腦一起用時，**跑 Docker 的那台電腦也要填 IP，不要填 `localhost`**。

## 網頁版和桌面圖形版在同一個聊天室

網頁版的使用者預設進到 `localhost` 這一間，也就是在跑 Docker 的電腦上、用桌面圖形版填 `localhost` 的那一間。

要讓其他電腦的桌面圖形版（填的是 IP）和網頁版在同一間，把 `docker-compose.yml` 裡 `web` 服務的 `ROOM_HOST` 改成那個 IP，再執行：

```bash
docker compose up -d --build
```

全部人都用網頁版的話，不需要做這一步。

## 停止與清除

```bash
docker compose down              # 停止，資料保留
docker compose down -v           # 停止並清空這個專案的資料庫（帳號和訊息都會不見）
docker compose down -v --rmi all # 再加上刪除這個專案建置的映像檔
```

### 清空整台電腦的 Docker

> **警告**：下面三行會刪除**整台電腦上所有**的 Docker 容器、資料和映像檔，包括其他專案的資料庫。只清這個專案的話，用上面的 `docker compose down -v --rmi all` 就夠了。

```bash
docker rm -f $(docker ps -aq)                # 刪除所有容器
docker volume rm -f $(docker volume ls -q)   # 刪除所有資料
docker rmi -f $(docker images -aq)           # 刪除所有映像檔
```

## 常見問題

| 狀況 | 處理方式 |
|------|------|
| 網頁顯示「無法連上這個網站」 | 執行 `docker compose ps` 確認 `web` 有在跑；沒有的話執行 `docker compose up -d --build`。網址是 `http://`，埠是 8082 |
| 其他裝置打不開網頁 | 不能用 `localhost`，要用 `http://<IP>:8082`；確認在同一個網路、防火牆沒擋 8082。學校 Wi-Fi 常會擋裝置互連，改用手機熱點 |
| 網頁顯示「與伺服器的連線中斷了」 | 伺服器重新啟動或關掉了，重新整理頁面再登入一次 |
| `docker compose` 找不到指令或連不到 Docker | Docker Desktop 沒有打開，打開後等它顯示 running |
| 登入時顯示「帳號或密碼錯誤」 | 帳號還沒註冊，或密碼打錯。第一次使用要按「註冊」 |
| 桌面版出現 `No module named '_tkinter'` | 這個 Python 沒有 Tkinter。Homebrew 的 Python 執行 `brew install python-tk`，或改用上面測出來能用的版本 |
| 桌面版出現 `macOS 15 (1507) or later required` | Apple 內建的 `python3` 和系統版本對不上，改用 Homebrew 的 `python3.13`，或更新 macOS |
| 桌面版的人看不到彼此的訊息 | 兩邊填的伺服器位址不一樣，見「所有人填的伺服器位址要一樣」 |
| 網頁版和桌面版的人看不到彼此的訊息 | 不在同一個聊天室，見「網頁版和桌面圖形版在同一個聊天室」 |
| 抽辯論題顯示「題庫目前沒有題目」 | 題庫沒匯入成功。執行 `docker compose down -v` 再 `docker compose up -d --build`（會清掉所有帳號和訊息） |
| 改了程式但沒有效果 | 要加 `--build` 重新建置：`docker compose up -d --build` |

## 專案結構

```text
final_project/
├── auth/                  驗證服務（port 8080）
│   └── auth.py            註冊、登入
├── server/                聊天服務（port 8081）
│   ├── server.py          訊息廣播、歷史訊息、抽辯論題、線上名單
│   └── topics.py          辯論題庫，第一次啟動時匯入資料庫
├── web/                   網頁版（port 8082）
│   ├── gateway.py         轉接服務：提供網頁，並把 WebSocket 轉成 TCP
│   └── static/index.html  網頁介面
├── client/
│   ├── gui_client.py      桌面圖形版
│   └── client.py          終端機版（最早的版本，仍可使用）
└── docker-compose.yml     MySQL、auth、server、web 四個服務
```

## 技術架構

```text
瀏覽器（電腦或手機）
   │  WebSocket
   ▼
web :8082  轉接服務 ──┐
                      │  TCP socket，一行一則 JSON
桌面圖形版 / 終端機版 ─┤
                      ├──→ auth   :8080   註冊、登入
                      └──→ server :8081   聊天、歷史訊息、抽辯論題
                                │
                                └──→ MySQL :3307   帳號、訊息、題庫
```

- **TCP socket + JSON**：每則訊息是一行 JSON，用 `type` 欄位分辨動作（`register`、`login`、`message`、`fetch`、`topic`、`online`）。TCP 是串流，所以用換行符號切出每一則訊息。
- **threading**：auth 和 server 每個連線開一條執行緒，同時服務多個使用者。
- **抽辯論題由 server 負責**：server 用 `ORDER BY RAND()` 從資料庫取一題，廣播給聊天室所有人並存進聊天紀錄。如果讓每個 client 自己抽，每個人看到的題目會不一樣。
- **網頁版轉接服務**：瀏覽器不能直接開 TCP socket，所以 `web/gateway.py` 把瀏覽器的 WebSocket 訊息轉成原本的協定送給 auth 和 server。auth、server、MySQL 都不需要為了網頁版修改，三種 client 可以在同一個聊天室裡聊天。轉接服務只用 Python 標準函式庫，WebSocket 的握手與資料框是照 RFC 6455 自己實作的。
- **網頁版不能冒充別人**：使用者名稱由轉接服務在登入成功後自己填入，不採用瀏覽器送來的值。
- **桌面圖形版的執行緒**：Tkinter 的畫面只能由主執行緒更新，所以接收訊息的背景執行緒把內容放進佇列，主執行緒每 0.1 秒取出來顯示。

| 技術 | 用途 |
|------|------|
| Python 3.11 | auth、server、web 的開發語言 |
| socket（TCP） | client 與 auth、server 之間的連線 |
| threading | 同時服務多個連線 |
| asyncio + WebSocket | 網頁版的轉接服務 |
| HTML / CSS / JavaScript | 網頁介面，沒有使用框架 |
| Tkinter | 桌面圖形版介面 |
| MySQL 8.0 | 儲存帳號、訊息與題庫 |
| Docker Compose | 一個指令啟動四個服務 |

## 注意事項

這是課程專案。為了方便開發，密碼目前以明文儲存在資料庫，資料庫連線設定也直接放在專案裡。不要用在正式環境，也不要使用自己在其他網站的真實密碼。
