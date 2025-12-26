import discord
from discord.ext import commands
from flask import Flask
from threading import Thread
import os

# --- 24時間稼働用のWebサーバー ---
app = Flask('')
@app.route('/')
def home():
    return "Bot is alive!"

def run():
    app.run(host='0.0.0.0', port=8080)

# --- ボットの設定 ---
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f'ログイン成功: {bot.user}')

# DM送信コマンド: !dm ユーザーID メッセージ内容
@bot.command()
async def dm(ctx, user_id: int, *, message):
    try:
        user = await bot.fetch_user(user_id)
        await user.send(message)
        await ctx.send(f"{user.name}さんに送信しました。")
    except Exception as e:
        await ctx.send(f"送信失敗: {e}")

# --- 実行部分 ---
if __name__ == "__main__":
    # Webサーバーを別スレッドで起動
    Thread(target=run).start()

    # Koyebの環境変数「TOKEN」から読み込む
    token = os.getenv("TOKEN")
    if token:
        bot.run(token)
    else:
        print("TOKENが見つかりません。Koyebの設定を確認してください。")
