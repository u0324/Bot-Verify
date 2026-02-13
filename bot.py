import os
import discord
from discord import app_commands
from discord.ext import commands, tasks
import psutil
import requests
import urllib.parse
import psycopg2
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import pytz
from sklearn.ensemble import RandomForestRegressor

# --- Secrets ---
DATABASE_URL = os.getenv('DATABASE_URL')
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
ANNICT_TOKEN = os.getenv('ANNICT_TOKEN')
YOUR_USER_ID = 1421704357983813744 

# --- 基本設定 ---
timezone_jp = pytz.timezone('Asia/Tokyo')
start_time = datetime.now(timezone_jp)

intents = discord.Intents.default()
intents.message_content = True 
intents.members = True # メンションやユーザー取得に必要

class ChulyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        init_db()
        self.check_reminders.start() # リマインダー監視開始
        await self.tree.sync() 

bot = ChulyBot()

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
        # リマインダーテーブル
        cur.execute('''CREATE TABLE IF NOT EXISTS reminders 
                       (id SERIAL PRIMARY KEY, user_id BIGINT, time TIMESTAMPTZ, content TEXT, interval_weeks INT)''')
    conn.commit()
    conn.close()

# リマインダー追加
def add_reminder(user_id, time, content, interval_weeks=0):
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("INSERT INTO reminders (user_id, time, content, interval_weeks) VALUES (%s, %s, %s, %s)",
                    (user_id, time, content, interval_weeks))
    conn.commit()
    conn.close()

# リマインダー取得
def get_user_reminders(user_id):
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT id, time, content, interval_weeks FROM reminders WHERE user_id = %s ORDER BY time ASC", (user_id,))
        rows = cur.fetchall()
    conn.close()
    return rows

# リマインダー削除
def delete_reminders(user_id, reminder_id=None):
    conn = get_db_connection()
    with conn.cursor() as cur:
        if reminder_id:
            cur.execute("DELETE FROM reminders WHERE id = %s", (reminder_id,))
        else:
            cur.execute("DELETE FROM reminders WHERE user_id = %s", (user_id,))
    conn.commit()
    conn.close()

# ==========================================
# 1. AIロジック (株価予測)
# ==========================================
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
# 2. バックグラウンドタスク (リマインダー監視)
# ==========================================
@tasks.loop(seconds=5.0)
async def check_reminders_task():
    now = datetime.now(timezone_jp)
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT id, user_id, time, content, interval_weeks FROM reminders WHERE time <= %s", (now,))
        due_reminders = cur.fetchall()
        
        for r_id, u_id, r_time, content, interval in due_reminders:
            user = bot.get_user(u_id)
            if user:
                embed = discord.Embed(title="⏰ リマインダー通知", description=content, color=0xff0000)
                embed.set_footer(text=f"設定時刻: {r_time.astimezone(timezone_jp).strftime('%Y/%m/%d %H:%M:%S')}")
                try:
                    await user.send(content=f"{user.mention} 通知が届きました。", embed=embed)
                except:
                    pass
            
            if interval > 0:
                new_time = r_time + timedelta(weeks=interval)
                cur.execute("UPDATE reminders SET time = %s WHERE id = %s", (new_time, r_id))
            else:
                cur.execute("DELETE FROM reminders WHERE id = %s", (r_id,))
    conn.commit()
    conn.close()

bot.check_reminders = check_reminders_task

# ==========================================
# 3. イベント
# ==========================================
@bot.event
async def on_ready():
    activity = discord.Activity(type=discord.ActivityType.watching, name="Uの生活")
    await bot.change_presence(status=discord.Status.online, activity=activity)
    print(f"✅ Online as {bot.user}")

# ==========================================
# 4. スラッシュコマンド
# ==========================================

# --- リマインダー機能 ---

@bot.tree.command(name="remind", description="指定日時にリマインドを設定します (最大3件)")
@app_commands.describe(date="YYYY/MM/DD", time="HH:MM:SS", content="内容")
async def remind(interaction: discord.Interaction, date: str, time: str, content: str):
    user_reminders = get_user_reminders(interaction.user.id)
    if len(user_reminders) >= 3:
        return await interaction.response.send_message("⚠️ リマインダーは最大3件までです。", ephemeral=True)
    
    try:
        dt = datetime.strptime(f"{date} {time}", "%Y/%m/%d %H:%M:%S")
        dt_jp = timezone_jp.localize(dt)
        if dt_jp < datetime.now(timezone_jp):
            return await interaction.response.send_message("⚠️ 過去の時間は設定できません。", ephemeral=True)
        
        add_reminder(interaction.user.id, dt_jp, content)
        await interaction.response.send_message(f"✅ 設定しました: {date} {time}\n内容: {content}")
    except ValueError:
        await interaction.response.send_message("⚠️ 日時形式が正しくありません (例: 2026/02/14 09:00:00)", ephemeral=True)

@bot.tree.command(name="remindweek", description="○週間おきに通知を設定します (最大3件)")
@app_commands.describe(weeks="何週間おきか", time="時刻 HH:MM:SS", content="内容")
async def remindweek(interaction: discord.Interaction, weeks: int, time: str, content: str):
    user_reminders = get_user_reminders(interaction.user.id)
    if len(user_reminders) >= 3:
        return await interaction.response.send_message("⚠️ リマインダーは最大3件までです。", ephemeral=True)
    
    try:
        now = datetime.now(timezone_jp)
        t = datetime.strptime(time, "%H:%M:%S").time()
        dt_jp = timezone_jp.localize(datetime.combine(now.date(), t))
        if dt_jp < now: dt_jp += timedelta(weeks=weeks)
        
        add_reminder(interaction.user.id, dt_jp, content, weeks)
        await interaction.response.send_message(f"✅ 定期リマインダーを設定しました: {weeks}週間おき {time}\n内容: {content}")
    except ValueError:
        await interaction.response.send_message("⚠️ 時刻形式が正しくありません (例: 09:00:00)", ephemeral=True)

@bot.tree.command(name="remindlist", description="設定中のリマインダーを表示します")
async def remindlist(interaction: discord.Interaction):
    data = get_user_reminders(interaction.user.id)
    if not data:
        return await interaction.response.send_message("🔔 設定中のリマインダーはありません。", ephemeral=True)
    
    embed = discord.Embed(title="🔔 リマインダーリスト", color=0x3498db)
    for i, r in enumerate(data):
        cycle = f" ({r[3]}週間おき)" if r[3] > 0 else ""
        embed.add_field(name=f"No.{i+1}", value=f"時間: {r[1].astimezone(timezone_jp).strftime('%Y/%m/%d %H:%M')}{cycle}\n内容: {r[2]}", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="remindstop", description="すべてのリマインダーをオフにします")
async def remindstop(interaction: discord.Interaction):
    delete_reminders(interaction.user.id)
    await interaction.response.send_message("✅ すべてのリマインダーを停止しました。")

# --- 元々の機能 (維持) ---

@bot.tree.command(name="prediction", description="カカポの株価を予測します")
async def prediction(interaction: discord.Interaction, price: int):
    if interaction.user.id != YOUR_USER_ID: return await interaction.response.send_message("⚠️ 開発者専用", ephemeral=True)
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
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="nuke", description="チャンネルをリセットします")
async def nuke(interaction: discord.Interaction, channel_id: str):
    if interaction.user.id != YOUR_USER_ID: return await interaction.response.send_message("⚠️ 開発者専用", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    try:
        target_channel = bot.get_channel(int(channel_id))
        new_channel = await target_channel.clone()
        await target_channel.delete()
        await new_channel.edit(position=target_channel.position)
        await interaction.followup.send(f"✅ <#{new_channel.id}> を再生成しました。")
    except Exception as e: await interaction.followup.send(f"❌ エラー: {e}")

@bot.tree.command(name="show_data", description="履歴を表示します")
async def show_data(interaction: discord.Interaction):
    df = load_history()
    if df.empty: return await interaction.response.send_message("📚 データなし")
    lines = []
    for row in df.iloc[::-1].head(10).itertuples():
        ts = row.timestamp.astimezone(timezone_jp).strftime('%m/%d %H:%M')
        lines.append(f"📁 {ts} | 価格: **{int(row.price)}**")
    await interaction.response.send_message(embed=discord.Embed(title="📚 最新履歴", description="\n".join(lines), color=0x2ecc71))

@bot.tree.command(name="status", description="Botの状況を確認します")
async def status(interaction: discord.Interaction):
    uptime = datetime.now(timezone_jp) - start_time
    embed = discord.Embed(title="📊 ステータス", color=0x3498db)
    embed.add_field(name="⏱️ 稼働時間", value=f"`{str(uptime).split('.')[0]}`")
    embed.add_field(name="📡 Ping", value=f"`{round(bot.latency * 1000)}ms`")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="calculation", description="簡単な計算")
async def calculation(interaction: discord.Interaction, num1: float, op: str, num2: float):
    try:
        if op == '+': res = num1 + num2
        elif op == '-': res = num1 - num2
        elif op == '*': res = num1 * num2
        elif op == '/': res = num1 / num2 if num2 != 0 else "Error"
        await interaction.response.send_message(f"🧮 結果: `{res}`")
    except: await interaction.response.send_message("Error")

@bot.tree.command(name="anime", description="今期アニメ")
async def anime(interaction: discord.Interaction, season: str):
    await interaction.response.defer()
    url = "https://api.annict.com/v1/works"
    params = {'access_token': ANNICT_TOKEN, 'filter_season': f"2026-{season}", 'sort_watchers_count': 'desc', 'per_page': 5}
    res = requests.get(url, params=params).json()
    works = res.get('works', [])
    if not works: return await interaction.followup.send("⚠️ なし")
    embeds = [discord.Embed(title=w['title'], url=w.get('official_site_url')) for w in works]
    await interaction.followup.send(embeds=embeds)

@bot.tree.command(name="delete_latest", description="最新データ削除")
async def delete_latest(interaction: discord.Interaction):
    if interaction.user.id != YOUR_USER_ID: return
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("DELETE FROM history WHERE timestamp = (SELECT MAX(timestamp) FROM history)")
    conn.commit(); conn.close()
    await interaction.response.send_message("✅ 削除しました")

bot.run(DISCORD_BOT_TOKEN)
