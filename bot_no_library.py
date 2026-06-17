import asyncio
import json
import websockets
import requests

# ⚙️ 設定
BOT_TOKEN = "ここにあなたのボットのトークンを貼り付けてね"
# 認証ヘッダー（Discordに命令を送る時に「私はこのボットです」と証明するもの）
HEADERS = {
    "Authorization": f"Bot {BOT_TOKEN}",
    "Content-Type": "application/json"
}

# 💬 メッセージを送信する関数（REST API を直接叩く）
def send_message(channel_id, content):
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    payload = {"content": content}
    # Discordのサーバーに直接「文字を送って！」とリクエストする
    response = requests.post(url, headers=HEADERS, json=payload)
    return response.json()

# 🌐 Discordと常時接続してイベントを受け取るメイン処理（Gateway通信）
async def discord_bot():
    # DiscordのGateway（連絡窓口）のURL
    gateway_url = "wss://gateway.discord.gg/?v=10&encoding=json"

    async with websockets.connect(gateway_url) as ws:
        print("Discord Gateway に接続しました。")

        # 1. 接続した直後、Discordから「Hello」というデータが届くのを待つ
        hello_message = await ws.recv()
        hello_data = json.loads(hello_message)
        print("DiscordからHelloを受信しました。")

        # 2. ボットの身分証明（Identify）のデータをDiscordに送る
        # (ここでどのインテント（権限）が欲しいかも数字で直接指定する)
        identify_payload = {
            "op": 2,  # Identifyを表すコード
            "d": {
                "token": BOT_TOKEN,
                "intents": 33280,  # GUILDS(1), GUILD_MESSAGES(512), MESSAGE_CONTENT(32768) の合計
                "properties": {
                    "os": "linux",
                    "browser": "my_custom_bot",
                    "device": "my_custom_bot"
                }
            }
        }
        await ws.send(json.dumps(identify_payload))
        print("身分証明（Identify）を送信しました。")

        # 3. ここから先は、Discordから流れてくるデータをずーーっと監視する（無限ループ）
        while True:
            # Discordからの生データを受信
            raw_data = await ws.recv()
            event = json.loads(raw_data)

            # イベントの種類をチェック
            event_type = event.get("t")

            # 📩 もし「メッセージが作成された（届いた）」というイベントだったら
            if event_type == "MESSAGE_CREATE":
                msg_data = event["d"]
                content = msg_data.get("content")
                author_name = msg_data["author"]["username"]
                channel_id = msg_data["channel_id"]

                # ボット自身の発言は無視する（無限ループ対策）
                if msg_data["author"].get("bot"):
                    continue

                print(f"【受信】{author_name}: {content}")

                # もし「!ping」と打たれたら、自作の送信関数で「Pong!」と返す
                if content == "!ping":
                    send_message(channel_id, "🏓 Discord.pyなしで返信成功！")
                    print("-> 返信を送信しました。")

# ボットの実行
asyncio.run(discord_bot())
