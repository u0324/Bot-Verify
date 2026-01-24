import os
import requests
import urllib.parse
import threading
import time
import json
import re
import numpy as np
import pandas as pd
from flask import Flask, jsonify, request
from datetime import datetime
from discord_interactions import verify_key, InteractionType, InteractionResponseType
from sklearn.linear_model import LinearRegression

app = Flask(__name__)

# --- Secrets ---
DISCORD_PUBLIC_KEY = os.getenv('DISCORD_PUBLIC_KEY')
ANNICT_TOKEN = os.getenv('ANNICT_TOKEN')
APPLICATION_ID = os.getenv('APPLICATION_ID') 

# --- 設定 ---
TARGET_URL = "https://money.takasumibot.com/trade/KAKAPO"
SEASON_MAP = {'spring': 'spring', 'summer': 'summer', 'fall': 'autumn', 'winter': 'winter'}

# --- グローバル変数 ---
price_history = []
history_lock = threading.Lock()

# ==========================================
# 1. 共通関数 (アニメ・株価)
# ==========================================

def get_anime_data(search_query=None, season_key=None, count=10):
    url = "https://api.annict.com/v1/works"
    params = {'access_token': ANNICT_TOKEN, 'sort_watchers_count': 'desc', 'per_page': count}
    if search_query:
        params['filter_title'] = search_query
    elif season_key:
        params['filter_season'] = f"{datetime.now().year}-{SEASON_MAP[season_key]}"
    try:
        res = requests.get(url, params=params, timeout=5).json()
        return res.get('works', [])
    except:
        return []

def fetch_stock_price_sync():
    """ブラウザを使わず、直接HTMLを取得して解析（超軽量版）"""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/104.1"
        }
        # サイトからHTMLを取得
        response = requests.get(TARGET_URL, headers=headers, timeout=10)
        content = response.text

        # 正規表現で「数字 コイン」の形を探す
        match = re.search(r'([\d,]+)\s*(コイン|coin)', content)
        if match:
            return float(match.group(1).replace(',', ''))
    except Exception as e:
        print(f"Fetch Error: {e}")
    return None

def analyze_logic(history):
    """予測ロジック（そのまま維持）"""
    if len(history) < 3:
        return "データ蓄積中...", 0, 50

    df = pd.DataFrame(history, columns=['price'])
    
    ma = df['price'].rolling(window=min(len(df), 5)).mean().iloc[-1]
    ma_sig = 1 if df['price'].iloc[-1] > ma else -1

    delta = df['price'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=min(len(df), 10)).mean().iloc[-1]
    loss = (-delta.where(delta < 0, 0)).rolling(window=min(len(df), 10)).mean().iloc[-1]
    rsi = 100 - (100 / (1 + gain / loss)) if loss != 0 else 50
    rsi_sig = -1 if rsi > 70 else (1 if rsi < 30 else 0)

    X = np.array(range(len(df))).reshape(-1, 1)
    y = df['price'].values
    model = LinearRegression().fit(X, y)
    predicted = model.predict([[len(df)]])[0]
    ml_sig = 1 if predicted > df['price'].iloc[-1] else -1

    score = ma_sig + rsi_sig + ml_sig
    diff = predicted - df['price'].iloc[-1]

    if score >= 2: status = "上昇トレンド (買い) 🚀"
    elif score == 1: status = "やや上昇 📈"
    elif score <= -2: status = "下落トレンド (売り) 📉"
    elif score == -1: status = "やや下落 📉"
    else: status = "横ばい・様子見 ➡️"

    return status, diff, rsi

# ==========================================
# 2. バックグラウンド & インタラクション (変更なし)
# ==========================================

def background_monitor():
    print("✅ 株価監視スレッド起動 (軽量モード)")
    while True:
        price = fetch_stock_price_sync()
        if price:
            with history_lock:
                price_history.append(price)
                if len(price_history) > 100:
                    price_history.pop(0)
            print(f"Monitor update: {price}")
        time.sleep(300)

def handle_yoso_prediction(interaction_token, application_id):
    price = fetch_stock_price_sync()
    if price:
        with history_lock:
            price_history.append(price)
            if len(price_history) > 100: price_history.pop(0)
            current_history = list(price_history)
    else:
        current_history = []

    if price and current_history:
        status, diff, rsi = analyze_logic(current_history)
        embed = {
            "title": "📊 カカポ株価 AI予想",
            "color": 0x00b0f4,
            "fields": [
                {"name": "💰 現在の株価", "value": f"**{price} コイン**", "inline": False},
                {"name": "🤖 AIの判断", "value": f"**{status}**", "inline": True},
                {"name": "🔮 予想変動", "value": f"{diff:+.2f} コイン", "inline": True},
                {"name": "🌡️ RSI", "value": f"{rsi:.1f}%", "inline": True}
            ],
            "footer": {"text": "Zeabur Flask Bot (Light Edition)"}
        }
        content = ""
    else:
        content = "⚠️ 株価の取得に失敗しました。"
        embed = None

    url = f"https://discord.com/api/v10/webhooks/{application_id}/{interaction_token}/messages/@original"
    json_data = {"content": content}
    if embed:
        json_data["embeds"] = [embed]
    requests.patch(url, json=json_data)

@app.route('/', methods=['POST'])
def interactions():
    signature = request.headers.get('X-Signature-Ed25519')
    timestamp = request.headers.get('X-Signature-Timestamp')
    if not signature or not timestamp or not verify_key(request.data, signature, timestamp, DISCORD_PUBLIC_KEY):
        return 'Unauthorized', 401

    data = request.json
    if data.get('type') == InteractionType.PING:
        return jsonify({'type': InteractionResponseType.PONG})

    if data.get('type') == InteractionType.APPLICATION_COMMAND:
        cmd_name = data['data']['name']
        options = {opt['name']: opt['value'] for opt in data['data'].get('options', [])}

        if cmd_name == 'anime':
            season = options.get('season')
            works = get_anime_data(season_key=season, count=10)
            if not works:
                return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'content': "⚠️ データなし"}})
            embeds = []
            for i, work in enumerate(works):
                work_url = work.get('official_site_url') or f"https://annict.com/works/{work.get('id')}"
                embed = {"title": f"{i+1}. {work['title']}", "url": work_url, "color": 0x3498db}
                if i == 0:
                    img = (work.get('images', {}).get('recommended_url') or work.get('images', {}).get('facebook_og_image_url'))
                    if img: embed["image"] = {"url": img}
                    embed["description"] = "🏆 今期の最注目作品"
                embeds.append(embed)
            return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'content': f"✅ **{datetime.now().year} {season} TOP10**", 'embeds': embeds}})

        elif cmd_name == 'service':
            work_name = options.get('work_name')
            works = get_anime_data(search_query=work_name, count=3)
            if not works: return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'content': "⚠️ 作品が見つかりません"}})
            embeds = []
            for work in works:
                q = urllib.parse.quote(work['title'])
                links = f"[Google検索](https://www.google.com/search?q={q}+アニメ)"
                embeds.append({"title": work['title'], "url": work.get('official_site_url') or "", "description": links, "color": 0xe74c3c})
            return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'content': f"🔍 **{work_name}**", 'embeds': embeds}})

        elif cmd_name == 'yoso':
            app_id = data.get('application_id')
            token = data.get('token')
            thread = threading.Thread(target=handle_yoso_prediction, args=(token, app_id))
            thread.start()
            return jsonify({'type': 5})

    return jsonify({'type': InteractionResponseType.PONG})

monitor_thread = threading.Thread(target=background_monitor, daemon=True)
monitor_thread.start()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
