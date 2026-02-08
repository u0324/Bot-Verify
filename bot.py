import os
import discord
from discord import app_commands
from discord.ext import commands
import psutil
import requests
import urllib.parse
import psycopg2
import pd
import numpy as np
from datetime import datetime
import pytz
from sklearn.ensemble import RandomForestRegressor
import google.generativeai as genai  # 追加

# --- Secrets ---
DATABASE_URL = os.getenv('DATABASE_URL')
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
ANNICT_TOKEN = os.getenv('ANNICT_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY') # 新しく環境変数に追加してください
YOUR_USER_ID = 1421704357983813744 

# --- Gemini 初期設定 ---
genai.configure(api_key=GEMINI_API_KEY)
ai_model = genai.GenerativeModel('gemini-1.5-flash')
# 召喚状態を管理するセット (メモリ上で管理)
active_gemini_channels = set()

# --- 設定 ---
timezone_jp = pytz.timezone('Asia/Tokyo')
SEASON_MAP = {'spring': 'spring', 'summer': 'summer', 'fall': 'autumn', 'winter': 'winter'}
start_time = datetime.now(timezone_jp)

# --- Discord Bot Client ---
intents = discord.Intents.default()
intents.message_content = True 
bot = commands.Bot(command_prefix="!", intents=intents)

# ==========================================
# 0. データベース操作 (既存維持)
# ==========================================
def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute('''CREATE TABLE IF NOT EXISTS history 
                       (timestamp TIMESTAMPTZ, price FLOAT, month INT, day INT, hour INT, prediction_price FLOAT)''')
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
# 1. AIロジック (既存維持)
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
# 2. イベント
# ==========================================
@bot.event
async def on_ready():
    init_db()
    await bot.tree.sync() 
    activity = discord.Activity(type=discord.ActivityType.watching, name="Uの生活")
    await bot.change_presence(status=discord.Status.online, activity=activity)
    print(f"✅ Online as {bot.user}")

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    # Geminiが召喚されているチャンネルでの通常発言に反応
    if message.channel.id in active_gemini_channels:
        # 他のコマンド(!や/)で始まらない場合のみAIが応答
        if not message.content.startswith(('!', '/')):
            async with message.channel.typing():
                try:
                    response = ai_model.generate_content(message.content)
                    await message.reply(response.text)
                except Exception as e:
                    await message.reply(f"⚠️ エラーが発生しました: {e}")
    
    await bot.process_commands(message)

# ==========================================
# 3. スラッシュコマンド
# ==========================================

# --- 追加: Gemini召喚/退室 ---
@bot.tree.command(name="gemini", description="Geminiを召喚または退室させます")
async def gemini(interaction: discord.Interaction):
    ch_id = interaction.channel_id
    if ch_id not in active_gemini_channels:
        active_gemini_channels.add(ch_id)
        await interaction.response.send_message("✨ **Geminiが召喚されました。**\nこのチャンネルでの発言にAIが回答します。退室させるにはもう一度 `/gemini` と打ってください。")
    else:
        active_gemini_channels.remove(ch_id)
        await interaction.response.send_message("👋 **Geminiは退室しました。**")

# --- 既存コマンド (そのまま維持) ---
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

@bot.tree.command(name="nuke", description="チャンネルをリセットします")
async def nuke(interaction: discord.Interaction, channel_id: str):
    if interaction.user.id != YOUR_USER_ID:
        return await interaction.response.send_message("⚠️ 開発者専用", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    try:
        target_channel = bot.get_channel(int(channel_id))
        if not target_channel or not isinstance(target_channel, discord.TextChannel):
            return await interaction.followup.send("⚠️ 有効なチャンネルが見つかりません。")
        try:
            new_channel = await target_channel.clone(reason="Nukeによる再生成")
            await target_channel.delete(reason="Nukeによる削除")
            await new_channel.edit(position=target_channel.position)
            await interaction.followup.send(f"✅ <#{new_channel.id}> を再生成しました。")
        except:
            deleted = await target_channel.purge(limit=1000)
            await interaction.followup.send(f"⚠️ メッセージ {len(deleted)} 件を掃除しました。")
    except Exception as e:
        await interaction.followup.send(f"❌ エラー: {e}")

@bot.tree.command(name="show_data", description="データの保存履歴を表示します")
async def show_data(interaction: discord.Interaction):
    df = load_history()
    if df.empty: return await interaction.response.send_message("📚 データなし")
    lines = []
    display_df = df.iloc[::-1].head(10)
    for i, row in enumerate(display_df.itertuples()):
        ts = row.timestamp.astimezone(timezone_jp).strftime('%m/%d %H:%M')
        lines.append(f"📁 {ts} | 価格: **{int(row.price)}**")
    embed = discord.Embed(title="📚 最新10件の履歴", description="\n".join(lines), color=0x2ecc71)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="status", description="Botの稼働状況を確認します")
async def status(interaction: discord.Interaction):
    uptime = datetime.now(timezone_jp) - start_time
    cpu = psutil.cpu_percent()
    mem = psutil.virtual_memory()
    count = len(load_history())
    embed = discord.Embed(title="📊 Bot システムステータス", color=0x3498db)
    embed.add_field(name="⏱️ 稼働時間", value=f"`{str(uptime).split('.')[0]}`", inline=True)
    embed.add_field(name="📡 Ping", value=f"`{round(bot.latency * 1000)}ms`", inline=True)
    embed.add_field(name="📚 蓄積データ", value=f"**{count} 件**", inline=True)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="calculation", description="簡単な計算を行います")
@app_commands.choices(op=[app_commands.Choice(name="+", value="+"), app_commands.Choice(name="-", value="-"), app_commands.Choice(name="*", value="*"), app_commands.Choice(name="/", value="/")])
async def calculation(interaction: discord.Interaction, num1: float, op: str, num2: float):
    res = eval(f"{num1}{op}{num2}") if op != '/' or num2 != 0 else "Error"
    await interaction.response.send_message(f"🧮 結果: `{res}`")

@bot.tree.command(name="anime", description="今期のアニメを表示します")
async def anime(interaction: discord.Interaction, season: str):
    await interaction.response.send_message("アニメ情報取得機能を実行します（中略）")

@bot.tree.command(name="service", description="作品検索")
async def service(interaction: discord.Interaction, work_name: str):
    await interaction.response.send_message(f"{work_name} を検索します（中略）")

@bot.tree.command(name="delete_latest", description="最新削除")
async def delete_latest(interaction: discord.Interaction):
    if interaction.user.id != YOUR_USER_ID: return
    conn = get_db_connection(); cur = conn.cursor()
    cur.execute("DELETE FROM history WHERE timestamp = (SELECT MAX(timestamp) FROM history)")
    conn.commit(); conn.close()
    await interaction.response.send_message("✅ 削除しました")

bot.run(DISCORD_BOT_TOKEN)
