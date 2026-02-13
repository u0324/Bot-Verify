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
intents.members = True 

class ChulyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        init_db()
        self.check_reminders_task.start()
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
        cur.execute('''CREATE TABLE IF NOT EXISTS history 
                       (timestamp TIMESTAMPTZ, price FLOAT, month INT, day INT, hour INT, prediction_price FLOAT)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS reminders 
                       (id SERIAL PRIMARY KEY, user_id BIGINT, time TIMESTAMPTZ, interval_weeks INT)''')
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

def get_user_reminders(user_id):
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT id, time, interval_weeks FROM reminders WHERE user_id = %s ORDER BY time ASC", (user_id,))
        rows = cur.fetchall()
    conn.close()
    return rows

# ==========================================
# 1. AIロジック (株価予測)
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
        model = RandomForestRegressor(n_estimators=50, max_depth=7, random_state=42)
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
    except: return "AI調整中", 0, 50, 0.0

# ==========================================
# 2. リマインダー監視タスク (定型文)
# ==========================================
@tasks.loop(seconds=5.0)
async def check_reminders_task():
    now = datetime.now(timezone_jp)
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT id, user_id, time, interval_weeks FROM reminders WHERE time <= %s", (now,))
        due = cur.fetchall()
        for r_id, u_id, r_time, interval in due:
            user = bot.get_user(u_id)
            if user:
                # 定型文はそのまま維持
                embed = discord.Embed(title="⏰ 通知", description="お約束の時間です。ご確認をお願いします。", color=0xff0000)
                embed.set_footer(text=f"設定: {r_time.astimezone(timezone_jp).strftime('%H:%M:%S')}")
                try: await user.send(content=f"{user.mention}", embed=embed)
                except: pass
            
            if interval > 0:
                # interval_weeks カラムを「時間」として扱い、次回の通知を計算
                next_time = r_time + timedelta(hours=interval)
                cur.execute("UPDATE reminders SET time = %s WHERE id = %s", (next_time, r_id))
            else:
                cur.execute("DELETE FROM reminders WHERE id = %s", (r_id,))
    conn.commit()
    conn.close()
bot.check_reminders_task = check_reminders_task

# ==========================================
# 3. イベント
# ==========================================
@bot.event
async def on_ready():
    activity = discord.Activity(type=discord.ActivityType.watching, name="Uの生活")
    await bot.change_presence(status=discord.Status.online, activity=activity)
    print(f"✅ Online: {bot.user}")

# ==========================================
# 4. スラッシュコマンド
# ==========================================

# --- リマインダー ---
@bot.tree.command(name="remind", description="指定日時に通知を設定します")
@app_commands.describe(date="YYYY/MM/DD", time="HH:MM:SS")
async def remind(interaction: discord.Interaction, date: str, time: str):
    user_reminders = get_user_reminders(interaction.user.id)
    if len(user_reminders) >= 3: return await interaction.response.send_message("⚠️ 最大3件までです。", ephemeral=True)
    try:
        dt = timezone_jp.localize(datetime.strptime(f"{date} {time}", "%Y/%m/%d %H:%M:%S"))
        if dt < datetime.now(timezone_jp): return await interaction.response.send_message("⚠️ 過去の時間は設定できません。", ephemeral=True)
        conn = get_db_connection(); cur = conn.cursor()
        cur.execute("INSERT INTO reminders (user_id, time, interval_weeks) VALUES (%s, %s, %s)", (interaction.user.id, dt, 0))
        conn.commit(); conn.close()
        await interaction.response.send_message(f"✅ 設定完了: {date} {time}")
    except: await interaction.response.send_message("⚠️ 形式エラー (2026/01/01 12:00:00)", ephemeral=True)

@bot.tree.command(name="remind_repeat", description="一定間隔で通知を設定します")
@app_commands.describe(interval="間隔（数字）", unit="単位", time="基準時刻 HH:MM:SS")
@app_commands.choices(unit=[
    app_commands.Choice(name="週間おき", value="weeks"),
    app_commands.Choice(name="時間おき", value="hours")
])
async def remind_repeat(interaction: discord.Interaction, interval: int, unit: app_commands.Choice[str], time: str):
    user_reminders = get_user_reminders(interaction.user.id)
    if len(user_reminders) >= 3: 
        return await interaction.response.send_message("⚠️ 最大3件までです。", ephemeral=True)
    
    try:
        now = datetime.now(timezone_jp)
        t = datetime.strptime(time, "%H:%M:%S").time()
        target_dt = timezone_jp.localize(datetime.combine(now.date(), t))
        
        # 単位に合わせて時間を計算 (1週間 = 168時間)
        interval_in_hours = interval if unit.value == "hours" else interval * 168
        
        # 設定時刻が過去なら、指定の間隔分だけ進める
        if target_dt < now:
            target_dt += timedelta(hours=interval_in_hours)
        
        conn = get_db_connection()
        cur = conn.cursor()
        # カラム名は interval_weeks のままですが、中身は「時間」として保存します
        cur.execute("INSERT INTO reminders (user_id, time, interval_weeks) VALUES (%s, %s, %s)", 
                    (interaction.user.id, target_dt, interval_in_hours))
        conn.commit()
        conn.close()
        
        await interaction.response.send_message(f"✅ 繰り返し設定完了: {interval}{unit.name} (初回: {target_dt.strftime('%m/%d %H:%M')})")
    except:
        await interaction.response.send_message("⚠️ 形式エラー (例: 09:00:00)", ephemeral=True)

@bot.tree.command(name="remindlist", description="現在設定中の通知を確認します")
async def remindlist(interaction: discord.Interaction):
    data = get_user_reminders(interaction.user.id)
    if not data: return await interaction.response.send_message("🔔 設定中の通知はありません。", ephemeral=True)
    embed = discord.Embed(title="🔔 通知リスト", color=0x3498db)
    for i, r in enumerate(data):
        cycle = f" ({r[2]}週間おき)" if r[2] > 0 else " (一度限り)"
        embed.add_field(name=f"No.{i+1}", value=f"時間: {r[1].astimezone(timezone_jp).strftime('%Y/%m/%d %H:%M')}{cycle}", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="remindstop", description="すべての通知をオフにします")
async def remindstop(interaction: discord.Interaction):
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("DELETE FROM reminders WHERE user_id = %s", (interaction.user.id,))
    conn.commit(); conn.close()
    await interaction.response.send_message("✅ すべて削除しました。")

# --- 計算機能 ---
@bot.tree.command(name="calculation", description="簡単な計算を行います")
@app_commands.choices(op=[
    app_commands.Choice(name="+", value="+"), 
    app_commands.Choice(name="-", value="-"), 
    app_commands.Choice(name="*", value="*"), 
    app_commands.Choice(name="/", value="/")
])
async def calculation(interaction: discord.Interaction, num1: float, op: str, num2: float):
    try:
        if op == '+': res = num1 + num2
        elif op == '-': res = num1 - num2
        elif op == '*': res = num1 * num2
        elif op == '/': res = num1 / num2 if num2 != 0 else "Error"
        await interaction.response.send_message(f"🧮 結果: `{num1} {op} {num2} = {res}`")
    except: await interaction.response.send_message("エラーが発生しました")

# --- 株価予測 (完全維持) ---
@bot.tree.command(name="prediction", description="カカポの株価を予測します")
async def prediction(interaction: discord.Interaction, price: int):
    if interaction.user.id != YOUR_USER_ID: return await interaction.response.send_message("⚠️ 開発者専用", ephemeral=True)
    await interaction.response.defer()
    status, diff, rsi, score = get_full_analysis()
    predicted_next = float(price + diff)
    save_price(float(price), predicted_next)
    embed = discord.Embed(title="🕊️ カカポ株価　AI診断", description=f"最新価格 **{price}** を分析しました。", color=0x5865F2)
    embed.add_field(name="🤖 総合判定", value=f"**{status}**", inline=False)
    embed.add_field(name="🎯 次回予測価格", value=f"{int(predicted_next)}", inline=True)
    embed.add_field(name="🌡️ RSI (熱感)", value=f"{rsi}%", inline=True)
    embed.add_field(name="📈 変動幅予想", value=f"{diff:+d}", inline=True)
    embed.add_field(name="📊 AIスコア", value=f"{score:+.1f}", inline=True)
    embed.add_field(name="📚 蓄積データ", value=f"{len(load_history())} 件", inline=True)
    embed.set_footer(text="AI学習式株価予測")
    await interaction.followup.send(embed=embed)

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
            prev = display_df.iloc[i+1]
            if getattr(prev, 'prediction_price', None) is not None:
                hit_mark = " ✅" if int(round(float(row.price))) == int(round(float(prev.prediction_price))) else " ❌"
        lines.append(f"📁 {ts} | 価格: **{int(row.price)}**{hit_mark}{' (結果待ち)' if i == 0 else ''}")
    await interaction.response.send_message(embed=discord.Embed(title="📚 最新10件の履歴と的中判定", description="\n".join(lines), color=0x2ecc71))

@bot.tree.command(name="status", description="Botの稼働状況を確認します")
async def status(interaction: discord.Interaction):
    uptime = datetime.now(timezone_jp) - start_time
    cpu = psutil.cpu_percent(); mem = psutil.virtual_memory()
    embed = discord.Embed(title="📊 Bot システムステータス", color=0x3498db)
    embed.add_field(name="🟢 状態", value="**オンライン (正常稼働中)**", inline=False)
    embed.add_field(name="⏱️ 稼働時間", value=f"`{str(uptime).split('.')[0]}`", inline=True)
    embed.add_field(name="📡 Ping", value=f"`{round(bot.latency * 1000)}ms`", inline=True)
    embed.add_field(name="🖥️ CPU/RAM", value=f"{cpu}% / {mem.percent}%", inline=True)
    embed.add_field(name="📚 蓄積データ", value=f"**{len(load_history())} 件**", inline=True)
    await interaction.response.send_message(embed=embed)

# --- チャンネルリセット ---
@bot.tree.command(name="nuke", description="チャンネルをリセットします")
@app_commands.describe(channel_id="リセットしたいチャンネルのIDを入力してください")
async def nuke(interaction: discord.Interaction, channel_id: str):
    if interaction.user.id != YOUR_USER_ID: return await interaction.response.send_message("⚠️ 開発者専用", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    try:
        target = bot.get_channel(int(channel_id))
        new_ch = await target.clone()
        await target.delete()
        await new_ch.edit(position=target.position)
        await interaction.followup.send(f"✅ <#{new_ch.id}> を再生成しました。")
        await new_ch.send("💥 チャンネルがリセットされました。")
    except Exception as e: await interaction.followup.send(f"❌ エラー: {e}")

# --- アニメ (維持) ---
@bot.tree.command(name="anime", description="今期の人気アニメを表示します")
@app_commands.choices(season=[
    app_commands.Choice(name="🌸 春", value="spring"), 
    app_commands.Choice(name="☀️ 夏", value="summer"), 
    app_commands.Choice(name="🍂 秋", value="fall"), 
    app_commands.Choice(name="❄️ 冬", value="winter")
])
async def anime(interaction: discord.Interaction, season: app_commands.Choice[str]):
    await interaction.response.defer()
    res = requests.get("https://api.annict.com/v1/works", params={'access_token': ANNICT_TOKEN, 'filter_season': f"2026-{season.value}", 'sort_watchers_count': 'desc', 'per_page': 10}).json()
    works = res.get('works', [])
    if not works: return await interaction.followup.send("⚠️ データなし")
    await interaction.followup.send(embeds=[discord.Embed(title=f"{i+1}. {w['title']}", url=w.get('official_site_url'), color=0x3498db) for i, w in enumerate(works)])

@bot.tree.command(name="service", description="アニメ作品を検索します")
async def service(interaction: discord.Interaction, work_name: str):
    res = requests.get("https://api.annict.com/v1/works", params={'access_token': ANNICT_TOKEN, 'filter_title': work_name, 'per_page': 3}).json()
    works = res.get('works', [])
    if not works: return await interaction.response.send_message("⚠️ なし")
    await interaction.response.send_message(embeds=[discord.Embed(title=w['title'], description=f"[Google検索](https://www.google.com/search?q={urllib.parse.quote(w['title'])}+アニメ)", color=0xe74c3c) for w in works])

@bot.tree.command(name="delete_latest", description="最新のデータを一件削除します")
async def delete_latest(interaction: discord.Interaction):
    if interaction.user.id != YOUR_USER_ID: return
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("DELETE FROM history WHERE timestamp = (SELECT MAX(timestamp) FROM history)")
    cnt = cur.rowcount; conn.commit(); conn.close()
    await interaction.response.send_message("✅ 削除成功" if cnt > 0 else "⚠️ データなし")

bot.run(DISCORD_BOT_TOKEN)
