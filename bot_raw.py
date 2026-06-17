import asyncio
import json
import sqlite3
import datetime
import urllib.request
import urllib.error
import websockets

# ==========================================
# 1. データベース設定（Render永続ディスク対応）
# ==========================================
DB_FILE = "/data/fortress_security_bot.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ban_history (
            user_id INTEGER, guild_id INTEGER, reason TEXT, PRIMARY KEY (user_id, guild_id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ip_verification (
            user_id INTEGER PRIMARY KEY, ip_hash TEXT, verified_at TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# ==========================================
# 2. 基本設定（環境に合わせて書き換えてね）
# ==========================================
BOT_TOKEN = "ここにあなたのボットのトークンを貼り付けてね"
ALERT_ROLE_NAME = "警戒"
LOG_CHANNEL_NAME = "防犯ログ"
VERIFY_CHANNEL_NAME = "認証部屋"
SKIP_ROLE_NAME = "管理者専用ロール"

CAPTCHA_IMAGE_URL = "https://example.com/your_captcha_image.png" 
CAPTCHA_ANSWER = "1234"

BAD_WORDS = ["荒らし", "あらし", "cheat", "チート", "スパム"]
HONEYPOT_USER_IDS = [123456789012345678, 987654321098765432]
REAL_ADMIN_NAMES = ["Mikan", "みかん"]

# システム内部用変数
user_msg_times = {}
lockdown_mode = False
deleted_channels_count = 0
sequence_number = None  # Discordとの通信同期用

# HTTP通信用の共通ヘッダー
HEADERS = {
    "Authorization": f"Bot {BOT_TOKEN}",
    "Content-Type": "application/json",
    "User-Agent": "DiscordBot (CustomRawEngine, 1.0)"
}

# ==========================================
# 3. Discord API を直接叩くための自作関数（REST API）
# ==========================================

def discord_api_request(method, endpoint, payload=None):
    """discord.pyの代わりにDiscordに直接命令を送る関数"""
    url = f"https://discord.com/api/v10{endpoint}"
    data = json.dumps(payload).encode("utf-8") if payload else None
    req = urllib.request.Request(url, data=data, headers=HEADERS, method=method)
    try:
        with urllib.request.urlopen(req) as res:
            return json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"APIエラー [{e.code}]: {e.reason} (Endpoint: {endpoint})")
        return None
    except Exception as e:
        print(f"通信エラー: {e}")
        return None

def send_message(channel_id, content=None, embed=None):
    payload = {}
    if content: payload["content"] = content
    if embed: payload["embeds"] = [embed]
    return discord_api_request("POST", f"/channels/{channel_id}/messages", payload)

def delete_message(channel_id, message_id):
    return discord_api_request("DELETE", f"/channels/{channel_id}/messages/{message_id}")

def add_role_to_member(guild_id, user_id, role_id):
    return discord_api_request("PUT", f"/guilds/{guild_id}/members/{user_id}/roles/{role_id}")

def timeout_member(guild_id, user_id, minutes):
    until = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=minutes)).isoformat()
    return discord_api_request("PATCH", f"/guilds/{guild_id}/members/{user_id}", {"communication_disabled_until": until})

def ban_user(guild_id, user_id, reason):
    return discord_api_request("PUT", f"/guilds/{guild_id}/bans/{user_id}", {"delete_message_seconds": 0, "reason": reason})


# ==========================================
# 4. 生データ（JSON）を解析して防犯処理をする心臓部
# ==========================================

async def handle_discord_event(event_type, data):
    global lockdown_mode, deleted_channels_count, user_msg_times
    
    # ------------------------------------------
    # 📩 メッセージ受信時（連投・禁止ワード・メンション・クイズ）
    # ------------------------------------------
    if event_type == "MESSAGE_CREATE":
        author = data.get("author", {})
        if author.get("bot"): return
        
        channel_id = data.get("channel_id")
        msg_id = data.get("id")
        guild_id = data.get("guild_id")
        content = data.get("content", "")
        user_id = author.get("id")

        # 【おとりDM検知】
        if not guild_id and int(user_id) in HONEYPOT_USER_IDS:
            # 本来はDMイベントの判別が必要だが、簡易的におとり垢への受信を検知
            print(f"おとりDM検知！送信者ID: {user_id}")
            return

        # チャンネル名を取得するために、チャンネル情報をAPIで取得
        channel_info = discord_api_request("GET", f"/channels/{channel_id}")
        channel_name = channel_info.get("name", "") if channel_info else ""

        # 【クイズ判定】
        if channel_name == VERIFY_CHANNEL_NAME:
            if content.strip() == CAPTCHA_ANSWER:
                send_message(channel_id, f"✅ <@{user_id}> 認証されました！", )
                delete_message(channel_id, msg_id)
            else:
                send_message(channel_id, f"❌ <@{user_id}> 数字が違います。", )
                delete_message(channel_id, msg_id)
            return

        # 【禁止ワード】
        if any(word in content for word in BAD_WORDS):
            delete_message(channel_id, msg_id)
            timeout_member(guild_id, user_id, 60)
            return

        # 【アンチスパム】
        now = datetime.datetime.now(datetime.timezone.utc)
        if user_id not in user_msg_times: user_msg_times[user_id] = []
        user_msg_times[user_id].append(now)
        user_msg_times[user_id] = [t for t in user_msg_times[user_id] if (now - t).total_seconds() <= 5]
        if len(user_msg_times[user_id]) >= 5:
            delete_message(channel_id, msg_id)
            timeout_member(guild_id, user_id, 120)
            return

    # ------------------------------------------
    # 📥 メンバー入室時（履歴・作成日・偽装・クイズ出題）
    # ------------------------------------------
    elif event_type == "GUILD_MEMBER_ADD":
        guild_id = data.get("guild_id")
        user = data.get("user", {})
        user_id = user.get("id")
        username = user.get("username")
        
        # サーバーの全ロールと全チャンネルを取得
        roles = discord_api_request("GET", f"/guilds/{guild_id}/roles") or []
        channels = discord_api_request("GET", f"/guilds/{guild_id}/channels") or []
        
        alert_role = next((r for r in roles if r["name"] == ALERT_ROLE_NAME), None)
        log_channel = next((c for c in channels if c["name"] == LOG_CHANNEL_NAME), None)
        verify_channel = next((c for c in channels if c["name"] == VERIFY_CHANNEL_NAME), None)

        # 管理者偽装チェック
        is_impostor = any(admin.lower() in username.lower() for admin in REAL_ADMIN_NAMES)

        # アカウント作成日チェック (Snowflake ID から時間を逆算するか、送られてくるデータを使用)
        is_new_account = True # 簡易的に新規判定（生データ解析時はcreated_atの計算が必要）

        # データベース確認
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM ban_history WHERE user_id = ?", (user_id,))
        ban_count = cursor.fetchone()[0]
        conn.close()

        if ban_count > 0 or is_new_account or is_impostor:
            if alert_role: add_role_to_member(guild_id, user_id, alert_role["id"])
            if log_channel:
                send_message(log_channel["id"], f"⚠️ 警戒対象入室: <@{user_id}> (BAN歴:{ban_count}回, 偽装疑い:{is_impostor})")

        # クイズ出題
        if verify_channel:
            embed = {
                "title": "🔒 セキュリティ認証",
                "description": "画像内の半角数字を入力してください。",
                "image": {"url": CAPTCHA_IMAGE_URL},
                "color": 15158332
            }
            send_message(verify_channel["id"], content=f"<@{user_id}>", embed=embed)

    # ------------------------------------------
    # 🔨 誰かがBANされた時（データベース記録）
    # ------------------------------------------
    elif event_type == "GUILD_BAN_ADD":
        guild_id = data.get("guild_id")
        user_id = data.get("user", {}).get("id")
        
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO ban_history (user_id, guild_id, reason) VALUES (?, ?, ?)", (user_id, guild_id, "外部サーバーでのBAN"))
        conn.commit()
        conn.close()
        print(f"【生記録】ユーザー {user_id} のBANを記録しました。")


# ==========================================
# 5. Discordの「連絡窓口」と24時間通信し続けるシステム（Gateway）
# ==========================================

async def send_heartbeat(ws, interval):
    """Discordに『生きてるよ』と送り続ける処理（最重要・ないと切断される）"""
    while True:
        await asyncio.sleep(interval / 1000)
        heartbeat_payload = {"op": 1, "d": sequence_number}
        await ws.send(json.dumps(heartbeat_payload))

async def main():
    global sequence_number
    gateway_url = "wss://gateway.discord.gg/?v=10&encoding=json"
    
    print("Discordの生回線に直接接続を試みます...")
    async with websockets.connect(gateway_url) as ws:
        # Helloを受け取る
        hello_msg = await ws.recv()
        hello_data = json.loads(hello_msg)
        heartbeat_interval = hello_data["d"]["heartbeat_interval"]
        
        # 生存確認タスクを裏側でスタート
        asyncio.create_task(send_heartbeat(ws, heartbeat_interval))
        
        # ボットのログイン情報を送信
        identify_payload = {
            "op": 2,
            "d": {
                "token": BOT_TOKEN,
                "intents": 33539,  # すべての必要インテントを計算したビットフラグ
                "properties": {"os": "linux", "browser": "raw", "device": "raw"}
            }
        }
        await ws.send(json.dumps(identify_payload))
        print("ログイン信号送信完了。防犯監視を開始します。")

        # 流れてくる生データをすべて捕まえて解析する無限ループ
        while True:
            try:
                raw_data = await ws.recv()
                event = json.loads(raw_data)
                
                # 同期番号の更新
                if event.get("s"): sequence_number = event["s"]
                
                op = event.get("op")
                if op == 0:  # 通常のイベントデータ
                    event_type = event.get("t")
                    data = event.get("d")
                    # 自作の防犯エンジンにデータを丸投げ
                    await handle_discord_event(event_type, data)
                    
            except Exception as e:
                print(f"受信ループ内でエラーが発生しました: {e}")
                await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
