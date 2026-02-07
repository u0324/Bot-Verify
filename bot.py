import os
import discord
from discord import app_commands
from discord.ext import commands
import psutil
import requests
import urllib.parse
import psycopg2
import pandas as pd
import numpy as np
from datetime import datetime
import pytz
from sklearn.ensemble import RandomForestRegressor

# --- Secrets ---
DATABASE_URL = os.getenv('DATABASE_URL')
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
ANNICT_TOKEN = os.getenv('ANNICT_TOKEN')
YOUR_USER_ID = 1421704357983813744 # あなたのID

# --- 設定 ---
timezone_jp = pytz.timezone('Asia/Tokyo')
SEASON_MAP = {'spring': 'spring', 'summer': 'summer', 'fall': 'autumn', 'winter': 'winter'}
start_time = datetime.now(timezone_jp)

# --- Discord Bot Client ---
intents = discord.Intents.default()
intents.message_content = True 
bot = commands.Bot(command_prefix="!", intents=intents)

# ==========================================
# 0. データベース操作
# ==========================================
def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute('''CREATE TABLE IF NOT EXISTS history 
                       (timestamp TIMESTAMPTZ, price FLOAT, month INT, day INT, hour INT, prediction_price FLOAT)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS settings 
                       (user_id TEXT PRIMARY KEY, is_notice_on BOOLEAN DEFAULT FALSE)''')
    conn.commit()
    conn.close()

def save_price(price, pred_price=None):
    now = datetime.now(timezone_jp)
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("INSERT INTO history (timestamp, price, month, day, hour, prediction_price) VALUES (%s, %s, %s, %s, %s, %s)",
                    (now, price, now.month, now.day, now.hour, pred_price))
    conn.commit()
    conn.close()

def load_history():
    conn = get_db_connection()
    df = pd.read_sql_query("SELECT * FROM history ORDER BY timestamp ASC", conn)
    conn.close()
    return df

# ==========================================
# 1. AIロジック
# ==========================================
def get_full_analysis():
    df = load_history()
    if len(df) < 10: return f"蓄積中({len(df)}/10)", 0, 50, 0.0
    
    df = df.copy()
    df['ma5'] = df['price'].rolling(window=5, min_periods=1).mean()
    df['deviation'] = (df['price'] - df['ma5']) / df['ma5'] * 100
    df['momentum'] = df['price'].diff(3).fillna(0)

    features = ['month', 'day', 'hour', 'deviation', 'momentum']
    X = df[features].values
    y = df['price'].values

    try:
        model = RandomForestRegressor(n_estimators=100, max_depth=7, random_state=42)
        model.fit(X, y)
        now = datetime.now(timezone_jp)
        last_row = df.iloc[-1]
        current_features = np.array([[now.month, now.day, now.hour, last_row['deviation'], last_row['momentum']]])
        pred_raw = model.predict(current_features)[0]
        
        delta = df['price'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=min(len(df), 14), min_periods=1).mean().iloc[-1]
        loss = (-delta.where(delta < 0, 0)).rolling(window=min(len(df), 14), min_periods=1).mean().iloc[-1]
        rsi = 100.0 - (100.0 / (1.0 + (gain / loss))) if loss != 0 else 50.0

        diff = int(round(pred_raw - df['price'].iloc[-1]))
        score = 0.0
        if diff >= 1: score += 1.0
        if rsi < 35: score += 1.5
        if rsi > 65: score -= 1.5

        if diff >= 5 or score >= 2.5: status = "強力な上昇サイン 🚀"
        elif diff >= 1: status = "緩やかな上昇見込み 📈"
        elif diff <= -5 or score <= -2.5: status = "下落注意 📉"
        else: status = "方向感の探り合い ➡️"
        return status, diff, int(round(rsi)), score
    except:
        return "AI調整中", 0, 50, 0.0

# ==========================================
# 2. イベント・監視機能
# ==========================================
@bot.event
async def on_ready():
    init_db()
    await bot.tree.sync() # これで説明文と選択肢を強制同期
    await bot.change_presence(status=discord.Status.invisible) # 隠れ身モード
    print(f"✅ Online as {bot.user}")

@bot.event
async def on_message(message):
    if message.author.bot: return
    # ギフト通知
    if "https://gift.takasumibot.com/" in message.content:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT is_notice_on FROM settings WHERE user_id = %s", (str(YOUR_USER_ID),))
            res = cur.fetchone()
            is_on = res[0] if res else False
        conn.close()
        if is_on:
            owner = await bot.fetch_user(YOUR_USER_ID)
            await owner.send(f"🎁 **ギフトリンク検知**\n{message.content}")
    await bot.process_commands(message)

# ==========================================
# 3. スラッシュコマンド
# ==========================================

# --- 開発者専用: 株価予測 ---
@bot.tree.command(name="prediction", description="カカポの株価を予測します")
async def prediction(interaction: discord.Interaction, price: int):
    if interaction.user.id != YOUR_USER_ID:
        return await interaction.response.send_message("⚠️ 開発者専用", ephemeral=True)
    await interaction.response.defer()
    status, diff, rsi, score = get_full_analysis()
    predicted_next = float(price + diff)
    save_price(float(price), predicted_next)
    count = len(load_history())
    embed = discord.Embed(title="🕊️ カカポ株価　AI診断", description=f"最新価格 **{price}** を分析しました。", color=0x5865F2)
    embed.add_field(name="🤖 総合判定", value=f"**{status}**", inline=False)
    embed.add_field(name="🎯 次回予測価格", value=f"{int(predicted_next)}", inline=True)
    embed.add_field(name="🌡️ RSI (熱感)", value=f"{rsi}%", inline=True)
    embed.add_field(name="📈 変動幅予想", value=f"{diff:+d}", inline=True)
    embed.add_field(name="📊 AIスコア", value=f"{score:+.1f}", inline=True)
    embed.add_field(name="📚 蓄積データ", value=f"{count} 件", inline=True)
    embed.set_footer(text="AI学習式株価予測")
    await interaction.followup.send(embed=embed)

# --- 開発者専用: 一括削除 (追加) ---
@bot.tree.command(name="nuke", description="指定したチャンネルのメッセージを一括削除します")
@app_commands.describe(channel_id="削除したいチャンネルのIDを入力してください")
async def nuke(interaction: discord.Interaction, channel_id: str):
    if interaction.user.id != YOUR_USER_ID:
        return await interaction.response.send_message("⚠️ 開発者専用", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    try:
        target_channel = bot.get_channel(int(channel_id))
        if target_channel and isinstance(target_channel, discord.TextChannel):
            deleted = await target_channel.purge(limit=100)
            await interaction.followup.send(f"✅ <#{channel_id}> のメッセージを {len(deleted)} 件削除しました。")
        else:
            await interaction.followup.send("⚠️ 有効なチャンネルIDが見つかりません。")
    except Exception as e:
        await interaction.followup.send(f"❌ エラー: {e}")

# --- 開発者専用: 通知スイッチ ---
@bot.tree.command(name="notice", description="ギフト通知のON/OFFを切り替えます")
async def notice(interaction: discord.Interaction):
    if interaction.user.id != YOUR_USER_ID: return
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("INSERT INTO settings (user_id, is_notice_on) VALUES (%s, TRUE) ON CONFLICT (user_id) DO UPDATE SET is_notice_on = NOT settings.is_notice_on RETURNING is_notice_on", (str(YOUR_USER_ID),))
        new_on = cur.fetchone()[0]
    conn.commit(); conn.close()
    await interaction.response.send_message(f"{'🔔 ON' if new_on else '🔕 OFF'} にしました")

# --- 履歴表示 ---
@bot.tree.command(name="show_data", description="データの保存履歴と的中判定を表示します")
async def show_data(interaction: discord.Interaction):
    df = load_history()
    if df.empty: return await interaction.response.send_message("📚 データなし")
    lines = []
    display_df = df.iloc[::-1].head(10)
    for i, row in enumerate(display_df.itertuples()):
        ts = row.timestamp.astimezone(timezone_jp).strftime('%m/%d %H:%M')
        hit_mark = ""
        if i > 0 and i + 1 < len(display_df):
            prev_data = display_df.iloc[i+1]
            p_price = getattr(prev_data, 'prediction_price', None)
            if p_price is not None:
                hit_mark = " ✅" if int(round(float(row.price))) == int(round(float(p_price))) else " ❌"
        lines.append(f"📁 {ts} | 価格: **{int(row.price)}**{hit_mark}{' (結果待ち)' if i == 0 else ''}")
    embed = discord.Embed(title="📚 最新10件の履歴と的中判定", description="\n".join(lines), color=0x2ecc71)
    await interaction.response.send_message(embed=embed)

# --- システム状況 (強化版) ---
@bot.tree.command(name="status", description="Botの稼働状況を確認します")
async def status(interaction: discord.Interaction):
    uptime = datetime.now(timezone_jp) - start_time
    cpu = psutil.cpu_percent()
    mem = psutil.virtual_memory()
    count = len(load_history())
    embed = discord.Embed(title="📊 Bot システムステータス", color=0x3498db)
    embed.add_field(name="🟢 状態", value="**オンライン (正常稼働中)**", inline=False)
    embed.add_field(name="⏱️ 稼働時間", value=f"`{str(uptime).split('.')[0]}`", inline=True)
    embed.add_field(name="📡 Ping", value=f"`{round(bot.latency * 1000)}ms`", inline=True)
    embed.add_field(name="🖥️ CPU/RAM", value=f"{cpu}% / {mem.percent}%", inline=True)
    embed.add_field(name="📚 蓄積データ", value=f"**{count} 件**", inline=True)
    await interaction.response.send_message(embed=embed)

# --- 四則演算 ---
@bot.tree.command(name="calculation", description="計算を行います")
@app_commands.choices(op=[app_commands.Choice(name="+", value="+"), app_commands.Choice(name="-", value="-"), app_commands.Choice(name="*", value="*"), app_commands.Choice(name="/", value="/")])
async def calculation(interaction: discord.Interaction, num1: float, op: str, num2: float):
    try:
        if op == '+': res = num1 + num2
        elif op == '-': res = num1 - num2
        elif op == '*': res = num1 * num2
        elif op == '/': res = num1 / num2 if num2 != 0 else "Error"
        await interaction.response.send_message(f"🔢 結果: `{num1} {op} {num2} = {res}`")
    except: await interaction.response.send_message("エラー")

# --- アニメ表示 (選択肢復活) ---
@bot.tree.command(name="anime", description="今期の人気アニメを表示します")
@app_commands.choices(season=[
    app_commands.Choice(name="🌸 春", value="spring"),
    app_commands.Choice(name="☀️ 夏", value="summer"),
    app_commands.Choice(name="🍂 秋", value="fall"),
    app_commands.Choice(name="❄️ 冬", value="winter")
])
async def anime(interaction: discord.Interaction, season: app_commands.Choice[str]):
    await interaction.response.defer()
    url = "https://api.annict.com/v1/works"
    params = {'access_token': ANNICT_TOKEN, 'filter_season': f"{datetime.now().year}-{season.value}", 'sort_watchers_count': 'desc', 'per_page': 10}
    res = requests.get(url, params=params).json()
    works = res.get('works', [])
    if not works: return await interaction.followup.send("⚠️ データなし")
    embeds = [discord.Embed(title=f"{i+1}. {w['title']}", url=w.get('official_site_url'), color=0x3498db) for i, w in enumerate(works)]
    await interaction.followup.send(embeds=embeds)

# --- 作品検索 ---
@bot.tree.command(name="service", description="アニメを検索します")
async def service(interaction: discord.Interaction, work_name: str):
    url = "https://api.annict.com/v1/works"
    res = requests.get(url, params={'access_token': ANNICT_TOKEN, 'filter_title': work_name, 'per_page': 3}).json()
    works = res.get('works', [])
    if not works: return await interaction.response.send_message("⚠️ なし")
    embeds = [discord.Embed(title=w['title'], description=f"[Google検索](https://www.google.com/search?q={urllib.parse.quote(w['title'])}+アニメ)", color=0xe74c3c) for w in works]
    await interaction.response.send_message(embeds=embeds)

# --- 最新一件削除 ---
@bot.tree.command(name="delete_latest", description="最新のデータを一件削除します")
async def delete_latest(interaction: discord.Interaction):
    if interaction.user.id != YOUR_USER_ID: return
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("DELETE FROM history WHERE timestamp = (SELECT MAX(timestamp) FROM history)")
    cnt = cur.rowcount; conn.commit(); conn.close()
    await interaction.response.send_message("✅ 削除成功" if cnt > 0 else "⚠️ データなし")

bot.run(DISCORD_BOT_TOKEN)
