import discord
from discord.ext import commands
import sqlite3
import datetime
import asyncio

# ==========================================
# 1. データベースの初期設定（Render永続ディスク対応）
# ==========================================
# Renderの無料永続ディスク（/data/）の中に保存
DB_FILE = "/data/fortress_security_bot.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # 他サーバーでのBAN履歴保存用
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ban_history (
            user_id INTEGER, guild_id INTEGER, reason TEXT, PRIMARY KEY (user_id, guild_id)
        )
    ''')
    # 【機能1】同一IPでの複数垢（サブ垢）紐付け用（将来的な拡張用）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ip_verification (
            user_id INTEGER PRIMARY KEY, ip_hash TEXT, verified_at TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# ==========================================
# 2. ボットの設定とインテント（v2.4以降の最適化仕様）
# ==========================================
intents = discord.Intents.default()
intents.members = True          # メンバー管理、作成日・名前偽装チェック
intents.moderation = True       # discord.py v2.4+ のBAN検知、ロックダウン用
intents.invites = True          # 招待リンク監視
intents.message_content = True  # メッセージ内容の監視（スパム・禁止ワード）
intents.guilds = True           # チャンネル・ロール更新の監視

bot = commands.Bot(command_prefix="!", intents=intents)

# ⚙️ サーバー環境に合わせてここを書き換えてね！
ALERT_ROLE_NAME = "警戒"
LOG_CHANNEL_NAME = "防犯ログ"
VERIFY_CHANNEL_NAME = "認証部屋"
SKIP_ROLE_NAME = "管理者専用ロール"  # クイズをスキップさせるロール

# 🖼️ 画像クイズ（キャプチャ認証）の設定
CAPTCHA_IMAGE_URL = "https://example.com/your_captcha_image.png"  # 数字が書かれた画像のURL
CAPTCHA_ANSWER = "1234"  # 画像に書かれている正解の半角数字

# 🤬 禁止ワードリスト
BAD_WORDS = ["荒らし", "あらし", "cheat", "チート", "スパム"]

# 🛑 セキュリティ連動用の設定変数
HONEYPOT_USER_IDS = [123456789012345678, 987654321098765432]  # 【機能3】おとり垢のユーザーID
REAL_ADMIN_NAMES = ["Mikan", "みかん"]  # 【機能5】本物の管理者の名前リスト

# システム内部用の一時変数
user_msg_times = {}
lockdown_mode = False
deleted_channels_count = 0


@bot.event
async def on_ready():
    print(f"【要塞防犯システム】discord.py v2.4+ 準拠モードで起動しました。")
    print(f"ログインアカウント: {bot.user.name} (ID: {bot.user.id})")
    print(f"データベース配置先: {DB_FILE}")
    print("------")


# ==========================================
# 3. メンバー入室時：偽装検知（機能5）＆ 基本チェック ＆ クイズ（機能6）
# ==========================================
@bot.event
async def on_member_join(member: discord.Member):
    global lockdown_mode
    guild = member.guild
    log_channel = discord.utils.get(guild.text_channels, name=LOG_CHANNEL_NAME)
    alert_role = discord.utils.get(guild.roles, name=ALERT_ROLE_NAME)

    # 【機能7】ロックダウン中の即時隔離
    if lockdown_mode:
        if log_channel: 
            await log_channel.send(f"🚨 **ロックダウン中の入室:** {member.mention} を警戒対象として隔離しました。")
        if alert_role: 
            await member.add_roles(alert_role)
        return

    # 👑 【ロールスキップ】「管理者専用ロール」を既に持っている場合はクイズ免除
    if discord.utils.get(member.roles, name=SKIP_ROLE_NAME):
        if log_channel: 
            await log_channel.send(f"👑 **認証スキップ:** {member.mention} は「{SKIP_ROLE_NAME}」を所持しているため認証をスキップしました。")
        return

    # 👥 【機能5】管理者の名前偽装（なりすまし）チェック
    is_impostor = False
    for admin_name in REAL_ADMIN_NAMES:
        if admin_name.lower() in member.name.lower() or (member.nick and admin_name.lower() in member.nick.lower()):
            is_impostor = True
            break

    # BAN履歴の確認
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM ban_history WHERE user_id = ?", (member.id,))
    ban_count = cursor.fetchone()[0]
    
    # 【機能1】同一IP（サブ垢）のチェック
    cursor.execute("SELECT ip_hash FROM ip_verification WHERE user_id = ?", (member.id,))
    user_ip = cursor.fetchone()
    alt_account_detected = False
    if user_ip:
        cursor.execute("SELECT COUNT(*) FROM ip_verification WHERE ip_hash = ? AND user_id != ?", (user_ip[0], member.id))
        if cursor.fetchone()[0] > 0:
            alt_account_detected = True
    conn.close()

    # アカウント作成日チェック
    now = datetime.datetime.now(datetime.timezone.utc)
    account_age = now - member.created_at
    is_new_account = account_age.days < 7

    # 🚨 警戒対象・なりすまし垢の処置とログ通知
    if ban_count > 0 or is_new_account or alt_account_detected or is_impostor:
        if alert_role:
            await member.add_roles(alert_role)
        if log_channel:
            msg = f"⚠️ **警戒対象が参加しました**:\nメンバー: {member.mention} ({member.name})\n"
            if is_impostor: msg += f"💀 **【重大警告】管理者の名前に酷似しています（なりすまし詐欺の可能性大）**\n"
            if ban_count > 0: msg += f"❌ 他サーバーでのBAN履歴: **{ban_count} 件**\n"
            if is_new_account: msg += f"⏳ 新規アカウント警告: 作成から **{account_age.days}日**（{member.created_at.strftime('%Y/%m/%d')}）\n"
            if alt_account_detected: msg += f"👥 複数アカウント警告: 過去に同一IPから別アカウントの参加歴あり\n"
            await log_channel.send(msg)

    # 📝 【機能6】画像付きのクイズ認証を出題
    verify_channel = discord.utils.get(guild.text_channels, name=VERIFY_CHANNEL_NAME)
    if verify_channel:
        embed = discord.Embed(
            title="🔒 サーバーセキュリティ認証",
            description="スパムBot自動入室対策の画像認証です。\n\n**【問題】**\n画像に書かれている数字を**半角**でこのチャンネルに入力してください。",
            color=discord.Color.brand_red()
        )
        embed.set_image(url=CAPTCHA_IMAGE_URL)
        await verify_channel.send(content=member.mention, embed=embed)


# ==========================================
# 4. メッセージ・DM受信時：おとり検知（機能3）＆ チャット監視（機能2,3,4,6）
# ==========================================
@bot.event
async def on_message(message: discord.Message):
    # 📩 【機能3】おとりアカウントへのDM（引き抜き・スパム）検知
    if message.guild is None:
        if message.channel.recipient and message.channel.recipient.id in HONEYPOT_USER_IDS:
            for guild in bot.guilds:
                member_in_guild = guild.get_member(message.author.id)
                if member_in_guild:
                    try:
                        await guild.ban(message.author, reason="おとりアカウントへのDMスパム（引き抜き・勧誘行為）")
                        log_channel = discord.utils.get(guild.text_channels, name=LOG_CHANNEL_NAME)
                        if log_channel:
                            await log_channel.send(f"🛡️ **おとりシステム発動:** おとり垢へのDM送信を検知したため、{message.author.mention} をサーバーから自動BANしました。")
                    except discord.Forbidden:
                        pass
            return

    if message.author.bot or not message.guild:
        return

    guild = message.guild
    author = message.author

    # 📝 【機能6】認証部屋での画像クイズ判定
    if message.channel.name == VERIFY_CHANNEL_NAME:
        if message.content.strip() == CAPTCHA_ANSWER:
            await message.channel.send(f"✅ {author.mention} 正解です！認証されました。", delete_after=5)
            await message.delete()
        else:
            await message.channel.send(f"❌ {author.mention} 数字が違います。もう一度半角数字で入力してください。", delete_after=5)
            await message.delete()
        return

    # 管理者・モデレーターは以下のテキスト荒らしチェックを免除
    if author.guild_permissions.administrator:
        await bot.process_commands(message)
        return

    # 🤬 【機能2】禁止ワードの自動検知＆即時タイムアウト
    if any(word in message.content for word in BAD_WORDS):
        await message.delete()
        try:
            await author.timeout(datetime.timedelta(hours=1), reason="禁止ワードの発言")
            log_channel = discord.utils.get(guild.text_channels, name=LOG_CHANNEL_NAME)
            if log_channel:
                await log_channel.send(f"🤬 **禁止ワード検知:** {author.mention} を1時間タイムアウトにしました。")
        except discord.Forbidden:
            pass
        return

    # ⚡ 【機能3】アンチスパム（5秒以内に5回発言でタイムアウト）
    now = datetime.datetime.now(datetime.timezone.utc)
    if author.id not in user_msg_times:
        user_msg_times[author.id] = []
    user_msg_times[author.id].append(now)
    user_msg_times[author.id] = [t for t in user_msg_times[author.id] if (now - t).total_seconds() <= 5]

    if len(user_msg_times[author.id]) >= 5:
        await message.delete()
        try:
            await author.timeout(datetime.timedelta(hours=2), reason="スパム連投行為")
            log_channel = discord.utils.get(guild.text_channels, name=LOG_CHANNEL_NAME)
            if log_channel:
                await log_channel.send(f"⚡ **スパム検知:** 連投を行った {author.mention} を2時間タイムアウトにしました。")
        except discord.Forbidden:
            pass
        return

    # 📢 【機能4】メンション乱用防止（1通に5人以上で削除＆タイムアウト）
    mention_count = len(message.mentions) + len(message.role_mentions)
    if message.mention_everyone:
        mention_count += 1

    if mention_count >= 5:
        await message.delete()
        try:
            await author.timeout(datetime.timedelta(minutes=30), reason="大量メンションの乱用")
            log_channel = discord.utils.get(guild.text_channels, name=LOG_CHANNEL_NAME)
            if log_channel:
                await log_channel.send(f"📢 **メンション乱用:** {author.mention} をメッセージ削除の上、30分タイムアウトにしました。")
        except discord.Forbidden:
            pass
        return

    await bot.process_commands(message)


# ==========================================
# 5. サーバー監視：BAN記録 ＆ 非公式招待削除
# ==========================================
@bot.event
async def on_member_ban(guild: discord.Guild, user: discord.User):
    try:
        ban_entry = await guild.fetch_ban(user)
        reason = ban_entry.reason if ban_entry.reason else "理由は未記入"
    except discord.NotFound:
        reason = "理由は未記入"

    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO ban_history (user_id, guild_id, reason) VALUES (?, ?, ?)", (user.id, guild.id, reason))
    conn.commit()
    conn.close()

@bot.event
async def on_invite_create(invite: discord.Invite):
    guild = invite.guild
    inviter = invite.inviter
    if inviter and not inviter.guild_permissions.administrator:
        try:
            await invite.delete()
            log_channel = discord.utils.get(guild.text_channels, name=LOG_CHANNEL_NAME)
            if log_channel:
                await log_channel.send(f"🛡️ **非公式招待リンクを削除:** {inviter.mention} による招待作成を自動で阻止しました。")
        except discord.Forbidden:
            pass


# ==========================================
# 6. 【機能4】内部反乱：ロール権限の自動ロールバック
# ==========================================
@bot.event
async def on_guild_role_update(before: discord.Role, after: discord.Role):
    guild = after.guild
    if not before.permissions.administrator and after.permissions.administrator:
        if after.name != SKIP_ROLE_NAME:
            try:
                await after.edit(permissions=before.permissions, reason="【防犯自動化】未許可の管理者権限付与を検知しロールバック")
                log_channel = discord.utils.get(guild.text_channels, name=LOG_CHANNEL_NAME)
                if log_channel:
                    await log_channel.send(f"🔄 **【内部防犯】** ロール「{after.name}」への管理者権限付与を検知したため、自動でロールバックしました。")
            except discord.Forbidden:
                pass


# ==========================================
# 7. 【機能7】大量チャンネル削除時の緊急ロックダウン
# ==========================================
@bot.event
async def on_guild_channel_delete(channel: discord.abc.GuildChannel):
    global deleted_channels_count, lockdown_mode
    guild = channel.guild
    deleted_channels_count += 1

    async def reset_count():
        global deleted_channels_count
        await asyncio.sleep(10)
        if deleted_channels_count > 0:
            deleted_channels_count -= 1

    bot.loop.create_task(reset_count())

    if deleted_channels_count >= 3 and not lockdown_mode:
        lockdown_mode = True
        log_channel = discord.utils.get(guild.text_channels, name=LOG_CHANNEL_NAME)
        if log_channel:
            await log_channel.send("🚨 ⚠️ **【緊急事態】連続チャンネル削除を検知。全テキストチャンネルをロックダウン（閲覧専用）します** ⚠️ 🚨")
        
        for txt_channel in guild.text_channels:
            try:
                perms = txt_channel.overwrites_for(guild.default_role)
                perms.send_messages = False
                await txt_channel.set_permissions(guild.default_role, overwrite=perms)
            except discord.Forbidden:
                pass


# ==========================================
# 8. 【機能8】ボットの追加・重大な監査ログ監視
# ==========================================
@bot.event
async def on_guild_audit_log_entry_create(entry: discord.AuditLogEntry):
    guild = entry.guild
    log_channel = discord.utils.get(guild.text_channels, name=LOG_CHANNEL_NAME)
    if not log_channel:
        return

    if entry.action == discord.AuditLogAction.bot_add:
        await log_channel.send(f"🤖 **監視ログ:** 新しいボット {entry.target.mention} が {entry.user.mention} によって追加されました。")


# 🔓 ロックダウン解除コマンド
@bot.command()
@commands.has_permissions(administrator=True)
async def unlock(ctx):
    global lockdown_mode
    lockdown_mode = False
    await ctx.send("🔓 緊急ロックダウンモードを解除しました。")


# 🚀 ボットトークンの貼り付け
BOT_TOKEN = "ここにあなたのボットのトークンを貼り付けてね"
bot.run(BOT_TOKEN)
