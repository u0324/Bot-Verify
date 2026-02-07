import os
import discord
from discord import app_commands
from discord.ext import commands
import psutil
import requests
import urllib.parse
import threading
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
YOUR_USER_ID = 1421704357983813744  # 数値型

# --- 設定 ---
timezone_jp = pytz.timezone('Asia/Tokyo')
SEASON_MAP = {'spring': 'spring', 'summer': 'summer', 'fall': 'autumn', 'winter': 'winter'}
start_time = datetime.now(timezone_jp)

# --- Discord Bot Client ---
intents = discord.Intents.default()
intents.message_content = True  # ギフトリンク検知に必須
bot = commands.Bot(command_prefix="!", intents=intents)

# ==========================================
# 0. データベース操作
# ==========================================
def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_db_connection()
    with conn.cursor() as cur:
        # 株価履歴テーブル
        cur.execute('''CREATE TABLE IF NOT EXISTS history 
                       (timestamp TIMESTAMPTZ, price FLOAT, month INT, day INT, hour INT, prediction_price FLOAT)''')
        # 通知設定用テーブル (user_idごとに保存)
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
# 1. AIロジック (既存機能維持)
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
# 2. イベント・監視機能 (新規：匿名ギフト通知)
# ==========================================
@bot.event
async def on_ready():
    init_db()
    await bot.tree.sync()
    print(f"✅ Logged in as {bot.user}")

@bot.event
async def on_message(message):
    if message.author.bot: return

    # ギフトリンク検知 (誰が受け取ったかバレないよう匿名性を確保)
    if "https://gift.takasumibot.com/" in message.content:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT is_notice_on FROM settings WHERE user_id = %s", (str(YOUR_USER_ID),))
            res = cur.fetchone()
            is_on = res[0] if res else False
        conn.close()

        if is_on:
            owner = await bot.fetch_user(YOUR_USER_ID)
            # 送信者の情報は含めず、リンクのみを転送
            await owner.send(f"🎁 **たかすみギフトリンクを検知！**\n{message.content}")

    await bot.process_commands(message)

# ==========================================
# 3. スラッシュコマンド (既存 + 新規)
# ==========================================

# --- [既存] 株価予測 ---
@bot.tree.command(name="prediction", description="カカポの株価を予測します")
async def prediction(interaction: discord.Interaction, price: int):
    if interaction.user.id != YOUR_USER_ID:
        return await interaction.response.send_message("⚠️ 開発者専用です", ephemeral=True)
    
    await interaction.response.defer()
    status, diff, rsi, score = get_full_analysis()
    predicted_next = float(price + diff)
    save_price(float(price), predicted_next)
    count = len(load_history())

    embed = discord.Embed(title="🕊️ カカポ株価 AI診断", color=0x5865F2)
    embed.add_field(name="🤖 総合判定", value=f"**{status}**", inline=False)
    embed.add_field(name="🎯 次回予測価格", value=f"{int(predicted_next)}", inline=True)
    embed.add_field(name="🌡️ RSI", value=f"{rsi}%", inline=True)
    embed.add_field(name="📈 変動予想", value=f"{diff:+d}", inline=True)
    embed.add_field(name="📚 蓄積データ", value=f"{count} 件", inline=True)
    await interaction.followup.send(embed=embed)

# --- [既存] 履歴表示 ---
@bot.tree.command(name="show_data", description="履歴と的中判定を表示")
async def show_data(interaction: discord.Interaction):
    df = load_history()
    if df.empty: return await interaction.response.send_message("データなし")
    
    lines = []
    display_df = df.iloc[::-1].head(10)
    for i, row in enumerate(display_df.itertuples()):
        ts = row.timestamp.astimezone(timezone_jp).strftime('%m/%d %H:%M')
        mark = ""
        if i == 0: mark = " (結果待ち)"
        elif i + 1 < len(display_df):
            prev_pred = getattr(display_df.iloc[i+1], 'prediction_price', None)
            if prev_pred and int(round(float(row.price))) == int(round(float(prev_pred))):
                mark = " ✅"
            else: mark = " ❌"
        lines.append(f"📁 {ts} | 価格: **{int(row.price)}**{mark}")

    embed = discord.Embed(title="📚 最新10件の履歴", description="\n".join(lines), color=0x2ecc71)
    await interaction.response.send_message(embed=embed)

# --- [既存] データ削除 ---
@bot.tree.command(name="delete_latest", description="最新のデータを1件削除")
async def delete_latest(interaction: discord.Interaction):
    if interaction.user.id != YOUR_USER_ID: return
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("DELETE FROM history WHERE timestamp = (SELECT MAX(timestamp) FROM history)")
    conn.commit(); conn.close()
    await interaction.response.send_message("✅ 最新データを削除しました。")

# --- [既存] 今期アニメ表示 ---
@bot.tree.command(name="anime", description="今期の人気アニメを表示")
async def anime(interaction: discord.Interaction, season: str):
    url = "https://api.annict.com/v1/works"
    params = {
        'access_token': ANNICT_TOKEN,
        'filter_season': f"{datetime.now().year}-{SEASON_MAP.get(season, 'spring')}",
        'sort_watchers_count': 'desc',
        'per_page': 10
    }
    res = requests.get(url, params=params).json()
    works = res.get('works', [])
    if not works: return await interaction.response.send_message("アニメが見つかりませんでした。")
    
    embeds = [discord.Embed(title=f"{i+1}. {w['title']}", url=w.get('official_site_url'), color=0x3498db) for i, w in enumerate(works)]
    await interaction.response.send_message(embeds=embeds)

# --- [既存] アニメ検索 (service) ---
@bot.tree.command(name="service", description="アニメ作品を検索します")
async def service(interaction: discord.Interaction, work_name: str):
    url = "https://api.annict.com/v1/works"
    res = requests.get(url, params={'access_token': ANNICT_TOKEN, 'filter_title': work_name, 'per_page': 3}).json()
    works = res.get('works', [])
    if not works: return await interaction.response.send_message("作品が見つかりませんでした。")
    embeds = [discord.Embed(title=w['title'], description=f"[Google検索](https://www.google.com/search?q={urllib.parse.quote(w['title'])}+アニメ)", color=0xe74c3c) for w in works]
    await interaction.response.send_message(embeds=embeds)

# --- [新規] 計算機能 ---
@bot.tree.command(name="calculation", description="四則演算を行います")
@app_commands.choices(op=[
    app_commands.Choice(name="+ (足し算)", value="+"),
    app_commands.Choice(name="- (引き算)", value="-"),
    app_commands.Choice(name="* (掛け算)", value="*"),
    app_commands.Choice(name="/ (割り算)", value="/")
])
async def calculation(interaction: discord.Interaction, num1: float, op: str, num2: float):
    try:
        if op == '+': res = num1 + num2
        elif op == '-': res = num1 - num2
        elif op == '*': res = num1 * num2
        elif op == '/': res = num1 / num2 if num2 != 0 else "0で割ることはできません"
        await interaction.response.send_message(f"🔢 計算結果: `{num1} {op} {num2} = {res}`")
    except:
        await interaction.response.send_message("計算エラーが発生しました。")

# --- [新規] ステータス確認 ---
@bot.tree.command(name="status", description="BotのCPU・メモリ・稼働状況を確認")
async def status(interaction: discord.Interaction):
    uptime = datetime.now(timezone_jp) - start_time
    cpu = psutil.cpu_percent()
    mem = psutil.virtual_memory().percent
    count = len(load_history())

    embed = discord.Embed(title="📊 Bot システムステータス", color=0x3498db)
    embed.add_field(name="⏱️ 稼働時間", value=str(uptime).split('.')[0], inline=False)
    embed.add_field(name="🖥️ CPU使用率", value=f"{cpu}%", inline=True)
    embed.add_field(name="🧠 メモリ使用率", value=f"{mem}%", inline=True)
    embed.add_field(name="📚 蓄積データ数", value=f"{count} 件", inline=True)
    embed.add_field(name="🛰️ 状況", value="オンライン (正常稼働中)", inline=False)
    await interaction.response.send_message(embed=embed)

# --- [新規] 通知設定 ---
@bot.tree.command(name="notice", description="ギフト通知のON/OFFを切り替え")
async def notice(interaction: discord.Interaction):
    if interaction.user.id != YOUR_USER_ID: return await interaction.response.send_message("⚠️ 権限がありません", ephemeral=True)
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("INSERT INTO settings (user_id, is_notice_on) VALUES (%s, TRUE) ON CONFLICT (user_id) DO UPDATE SET is_notice_on = NOT settings.is_notice_on RETURNING is_notice_on", (str(YOUR_USER_ID),))
        new_on = cur.fetchone()[0]
    conn.commit(); conn.close()
    await interaction.response.send_message(f"{'🔔 通知をON' if new_on else '🔕 通知をOFF'} にしました。")

bot.run(DISCORD_BOT_TOKEN)
