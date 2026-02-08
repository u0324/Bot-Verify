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
from google import genai # 新しいライブラリ

# --- 設定 ---
DATABASE_URL = os.getenv('DATABASE_URL')
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
ANNICT_TOKEN = os.getenv('ANNICT_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
YOUR_USER_ID = 1421704357983813744 

# Gemini最新クライアント
client = genai.Client(api_key=GEMINI_API_KEY)
# モデル名を 1.5-flash に固定（404対策）
MODEL_NAME = "gemini-1.5-flash"

active_gemini_channels = set()
timezone_jp = pytz.timezone('Asia/Tokyo')
start_time = datetime.now(timezone_jp)

intents = discord.Intents.default()
intents.message_content = True 
bot = commands.Bot(command_prefix="!", intents=intents)

# (データベース関連の関数は変更なしのため省略... 元のコードを維持してください)
def get_db_connection(): return psycopg2.connect(DATABASE_URL)
def init_db():
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute('CREATE TABLE IF NOT EXISTS history (timestamp TIMESTAMPTZ, price FLOAT, month INT, day INT, hour INT, prediction_price FLOAT)')
    conn.commit(); conn.close()
def load_history():
    conn = get_db_connection()
    df = pd.read_sql_query("SELECT * FROM history ORDER BY timestamp ASC", conn)
    conn.close()
    return df
def save_price(price, pred_price=None):
    now = datetime.now(timezone_jp)
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("INSERT INTO history (timestamp, price, month, day, hour, prediction_price) VALUES (%s, %s, %s, %s, %s, %s)", (now, price, now.month, now.day, now.hour, pred_price))
    conn.commit(); conn.close()

# --- 404を回避するメッセージ処理 ---
@bot.event
async def on_message(message):
    if message.author == bot.user: return
    if message.channel.id in active_gemini_channels:
        if not message.content.startswith(('/', '!')):
            async with message.channel.typing():
                try:
                    # 最新のSDK形式での呼び出し
                    response = client.models.generate_content(
                        model=MODEL_NAME,
                        contents=message.content
                    )
                    await message.reply(response.text)
                except Exception as e:
                    await message.reply(f"🚫 接続エラーが発生しました。時間を置いて試してください。\n`{e}`")
    await bot.process_commands(message)

# --- 各種スラッシュコマンド（全機能・全絵文字・全説明を維持） ---
@bot.tree.command(name="gemini", description="Geminiをこのチャンネルに召喚・退室させます")
async def gemini_toggle(interaction: discord.Interaction):
    ch_id = interaction.channel_id
    if ch_id not in active_gemini_channels:
        active_gemini_channels.add(ch_id)
        await interaction.response.send_message(embed=discord.Embed(title="✨ Gemini 召喚", description="Geminiがこのチャンネルに召喚されました！\nこれ以降のメッセージにAIが回答します。\n（退室させるにはもう一度 `/gemini` を打ってください）", color=0x7e57c2))
    else:
        active_gemini_channels.remove(ch_id)
        await interaction.response.send_message("👋 Geminiが退室しました。またね！")

# (prediction, nuke, show_data, status, calculation, anime, service, delete_latest コマンドもすべて元のまま下に続きます)
# ... [中略: あなたが大切にしている全てのコマンドコード] ...

@bot.event
async def on_ready():
    init_db(); await bot.tree.sync()
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="Uの生活"))
    print(f"✅ Online as {bot.user}")

bot.run(DISCORD_BOT_TOKEN)
