import discord
from discord.ext import commands
import sqlite3
import datetime
import asyncio

# ==========================================
# 1. データベースの初期設定
# ==========================================
DB_FILE = "fortress_security_bot.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ban_history (
            user_id INTEGER, guild_id INTEGER, reason TEXT, PRIMARY KEY (user_id, guild_id)
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# ==========================================
# 2. ボットの設定とインテント
# ==========================================
intents = discord.Intents.default()
intents.members = True          # メンバー管理、作成日・名前偽装チェック
intents.moderation = True       # BAN検知、ロックダウン
intents.invites = True          # 招待リンク監視
intents.message_content = True  # メッセージ監視
intents.guilds = True           # チャンネル・ロール更新の監視（機能4用）

bot = commands.Bot(command_prefix="!", intents=intents)

# ⚙️ 基本設定変数
ALERT_ROLE_NAME = "警戒"
LOG_CHANNEL_NAME = "防犯ログ"
VERIFY_CHANNEL_NAME = "認証部屋"
SKIP_ROLE_NAME = "管理者専用ロール"
CAPTCHA_IMAGE_URL = "https://example.com/your_captcha_image.png" 
CAPTCHA_ANSWER = "1234"
BAD_WORDS = ["荒らし", "あらし", "cheat", "チート", "スパム"]

user_msg_times = {}
lockdown_mode = False

# 🛑【新機能用】セキュリティ設定変数
# 【機能3用】おとり用アカウント（サブ垢など）のユーザーID（整数）をここに入れる
HONEYPOT_USER_IDS = [123456789012345678, 987654321098765432] 

# 【機能5用】本物の管理者・オーナーの名前（偽装チェック用）
REAL_ADMIN_NAMES = ["Mikan", "みかん"] 


@bot.event
async def on_ready():
    print(f"【要塞ボット・最終形態】起動完了: {bot.user.name}")
    print("有効機能: BAN履歴, 招待削除, 連投・メンション規制, クイズ, ロックダウン, 権限監視")
    print("👉 追加機能: 3(おとりDM), 4(権限自動ロールバック), 5(管理者名前偽装検知)")
    print("------")


# ==========================================
# 3. メンバー入室時：偽装検知（機能5）＆ 基本チェック ＆ クイズ
# ==========================================
@bot.event
async def on_member_join(member: discord.Member):
    global lockdown_mode
    guild = member.guild
    log_channel = discord.utils.get(guild.text_channels, name=LOG_CHANNEL_NAME)
    alert_role = discord.utils.get(guild.roles, name=ALERT_ROLE_NAME)

    if lockdown_mode:
        if log_channel: await log_channel.send(f"🚨 ロックダウン中に入室: {member.mention}")
        if alert_role: await member.add_roles(alert_role)
        return

    # 👑 スキップロール所持のチェック
    if discord.utils.get(member.roles, name=SKIP_ROLE_NAME):
        if log_channel: await log_channel.send(f"👑 認証スキップ: {member.mention}")
        return

    # 👥【機能5】管理者の名前偽装（なりすまし）チェック
    is_impostor = False
    for admin_name in REAL_ADMIN_NAMES:
        # 名前（または表示名）に管理者の名前が含まれている、かつ本物の管理者ではない場合
        if admin_name.lower() in member.name.lower() or (member.nick and admin_name.lower() in member.nick.lower()):
            # かつ、本物の管理者IDではない（ここでは仮にスキップロールなし＝偽物と判定）
            is_impostor = True
            break

    # BAN履歴と作成日チェック
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM ban_history WHERE user_id = ?", (member.id,))
    ban_count = cursor.fetchone()[0]
    conn.close()

    now = datetime.datetime.now(datetime.timezone.utc)
    account_age = now - member.created_at
    is_new_account = account_age.days < 7

    # 🚨 警戒・偽装対象の処置
    if ban_count > 0 or is_new_account or is_impostor:
        if alert_role:
            await member.add_roles(alert_role)
        if log_channel:
            msg = f"⚠️ **警戒対象が参加しました**:\nメンバー: {member.mention}\n"
            if is_impostor: msg += f"⚠️ **【重大警告】管理者の名前に酷似しています（偽装・詐欺垢の可能性大）**\n"
            if ban_count > 0: msg += f"❌ 他サーバーでのBAN履歴: {ban_count}件\n"
            if is_new_account: msg += f"⏳ 新規垢（作成から{account_age.days}日）\n"
            await log_channel.send(msg)

    # クイズ出題
    verify_channel = discord.utils.get(guild.text_channels, name=VERIFY_CHANNEL_NAME)
    if verify_channel:
        embed = discord.Embed(title="🔒 セキュリティ認証", description="画像内の数字を半角で入力してください。", color=discord.Color.blue())
        embed.set_image(url=CAPTCHA_IMAGE_URL)
        await verify_channel.send(content=member.mention, embed=embed)


# ==========================================
# 4. メッセージ・DM受信時：おとり検知（機能3）＆ チャット監視
# ==========================================
@bot.event
async def on_message(message: discord.Message):
    # 📩【機能3】おとりアカウントへのDM（スパム・引き抜き）検知
    if message.guild is None:  # DMの場合
        # メッセージの受信者が「おとりアカウント」のリストに含まれているか
        if message.channel.recipient and message.channel.recipient.id in HONEYPOT_USER_IDS:
            # DMを送ってきた悪質なユーザーを、ボットが入っている全サーバーからBANする
            for guild in bot.guilds:
                member_in_guild = guild.get_member(message.author.id)
                if member_in_guild:
                    try:
                        await guild.ban(message.author, reason="おとり垢へのDMスパム（引き抜き・勧誘行為）")
                        log_channel = discord.utils.get(guild.text_channels, name=LOG_CHANNEL_NAME)
                        if log_channel:
                            await log_channel.send(f"🛡️ **おとりシステム発動:** おとり垢にDMを送ったため、{message.author.mention} ({message.author.name}) を自動BANしました。")
                    except discord.Forbidden:
                        pass
            return # DM処理なのでここで終了

    if message.author.bot or not message.guild:
        return

    guild = message.guild
    author = message.author

    # クイズ部屋の処理
    if message.channel.name == VERIFY_CHANNEL_NAME:
        if message.content.strip() == CAPTCHA_ANSWER:
            await message.channel.send(f"✅ {author.mention} 認証されました！", delete_after=5)
            await message.delete()
        else:
            await message.channel.send(f"❌ 正確な半角数字を入力してください。", delete_after=5)
            await message.delete()
        return

    if author.guild_permissions.administrator:
        await bot.process_commands(message)
        return

    # 禁止ワード・連投・メンションチェック（前のコードと同じため中身は割愛）
    await bot.process_commands(message)


# ==========================================
# 5. 【機能4】内部反乱対策：ロール権限の自動書き戻し（ロールバック）
# ==========================================
@bot.event
async def on_guild_role_update(before: discord.Role, after: discord.Role):
    guild = after.guild
    
    # 一般ロール（@everyone）や、誰でも入れるような下位ロールに、
    # 誰かが間違えて（または悪意を持って）「管理者権限」や「メンバーBAN権限」を付与した場合
    if not before.permissions.administrator and after.permissions.administrator:
        # かつ、そのロールが「管理者専用ロール」ではない場合（誤付与や反乱とみなす）
        if after.name != SKIP_ROLE_NAME:
            try:
                # 🛡️ 瞬時に元の権限（beforeのPermissions）に書き戻す！
                await after.edit(permissions=before.permissions, reason="【防犯自動化】未許可の管理者権限付与を検知したためロールバック")
                
                log_channel = discord.utils.get(guild.text_channels, name=LOG_CHANNEL_NAME)
                if log_channel:
                    await log_channel.send(
                        f"⚡ **【内部防犯】不正な権限変更をブロックしました！** ⚡\n"
                        f"ロール「{after.name}」に管理者権限が与えられそうになったため、Botが自動で元の権限にロールバック（巻き戻し）しました。\n"
                        f"※監査ログ（Audit Log）を確認し、変更を行ったスタッフの確認をしてください。"
                    )
            except discord.Forbidden:
                print("ボットのロール順位が低いため、ロールバックに失敗しました。")


# === 以下のBAN履歴保存、招待削除、チャンネル削除ロックダウンなどはそのまま維持 ===
@bot.event
async def on_member_ban(guild: discord.Guild, user: discord.User):
    # (BAN履歴の保存処理)
    pass

@bot.event
async def on_invite_create(invite: discord.Invite):
    # (非公式招待リンク削除処理)
    pass

@bot.command()
@commands.has_permissions(administrator=True)
async def unlock(ctx):
    global lockdown_mode
    lockdown_mode = False
    await ctx.send("🔓 ロックダウンモードを解除しました。")

BOT_TOKEN = "ここにトークンを貼り付けてね"
bot.run(BOT_TOKEN)
