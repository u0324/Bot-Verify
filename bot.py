import os
import requests
import urllib.parse
import threading
import time
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
        params['filter_season'] = f"{datetime.now().year}-{SEASON_MAP.get(season_key, 'spring')}"
    try:
        res = requests.get(url, params=params, timeout=10).json()
        return res.get('works', [])
    except:
        return []

def fetch_stock_price_sync():
    """Vercel等のブロックを回避し、確実に数字を抽出する軽量スクレイピング"""
    try:
        # 人間のブラウザ（Chrome）を詳細に模倣
        session = requests.Session()
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "ja,en-US;q=0.7,en;q=0.3",
            "Referer": "https://www.google.com/",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1"
        }
        
        response = session.get(TARGET_URL, headers=headers, timeout=15)
        if response.status_code != 200:
            print(f"Debug: Access Denied. Status: {response.status_code}")
            return None

        # HTMLタグを除去して、余分な空白を詰める
        clean_text = re.sub(r'<[^>]+>', ' ', response.text)
        clean_text = ' '.join(clean_text.split())

        # 「数字 ＋ コイン/coin/Coin」を、全角・半角・カンマ混じり問わず抽出
        # 前後の文脈に関わらず、最初に見つかった「お金の単位っぽい数字」を狙います
        match = re.search(r'([\d,.]+)\s*(?:コイン|coin|Coin)', clean_text)
        
        if match:
            # カンマを除去して数値化
            price_val = float(match.group(1).replace(',', ''))
            return price_val
        else:
            # 取得失敗時にログでサイトの中身を確認できるようにする
            print(f"Debug: Pattern missing. Raw text preview: {clean_text[:150]}")
    except Exception as e:
        print(f"Fetch Error: {e}")
    return None

def analyze_logic(history):
    """予測ロジック (メモリ効率重視)"""
    if len(history) < 3:
        return "データ蓄積中...", 0.0, 50.0

    df = pd.DataFrame(history, columns=['price'])
    
    # 1. 移動平均
    ma = df['price'].rolling(window=min(len(df), 5)).mean().iloc[-1]
    ma_sig = 1 if df['price'].iloc[-1] > ma else -1

    # 2. RSI
    delta = df['price'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=min(len(df), 10)).mean().iloc[-1]
    loss = (-delta.where(delta < 0, 0)).rolling(window=min(len(df), 10)).mean().iloc[-1]
    rsi = 100.0 - (100.0 / (1.0 + gain/loss)) if loss != 0 else 50.0
    rsi_sig = -1 if rsi > 70 else (1 if rsi < 30 else 0)

    # 3. 線形回帰
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
# 2. バックグラウンド & Webhook (非同期)
# ==========================================

def background_monitor():
    print("✅ 株価監視スレッド起動 (軽量スクレイピングモード)")
    while True:
        price = fetch_stock_price_sync()
        if price:
            with history_lock:
                price_history.append(price)
                if len(price_history) > 100:
                    price_history.pop(0)
            print(f"Monitor update: {price}")
        time.sleep(900)

def handle_yoso_prediction(token, application_id):
    price = fetch_stock_price_sync()
    
    with history_lock:
        if price:
            price_history.append(price)
            if len(price_history) > 100: price_history.pop(0)
        current_history = list(price_history)

    if price and current_history:
        status, diff, rsi = analyze_logic(current_history)
        embed = {
            "title": "📊 カカポ株価 AI予想",
            "color": 0x00b0f4,
            "fields": [
                {"name": "💰 現在の株価", "value": f"**{price:,.1f} コイン**", "inline": False},
                {"name": "🤖 AIの判断", "value": f"**{status}**", "inline": True},
                {"name": "🔮 予想変動", "value": f"{diff:+.2f} コイン", "inline": True},
                {"name": "🌡️ RSI", "value": f"{rsi:.1f}%", "inline": True}
            ],
            "footer": {"text": f"Light Mode Active | {datetime.now().strftime('%H:%M:%S')}"}
        }
        json_data = {"embeds": [embed]}
    else:
        json_data = {"content": "⚠️ 株価データの取得に失敗しました。サイト側のセキュリティ制限にかかっている可能性があります。"}

    url = f"https://discord.com/api/v10/webhooks/{application_id}/{token}/messages/@original"
    requests.patch(url, json=json_data)

# ==========================================
# 3. Flask Endpoint
# ==========================================

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
            works = get_anime_data(season_key=season)
            if not works:
                return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'content': "⚠️ データが見つかりませんでした。"}})
            
            embeds = []
            for i, work in enumerate(works[:10]):
                url = work.get('official_site_url') or f"https://annict.com/works/{work['id']}"
                embed = {"title": f"{i+1}. {work['title']}", "url": url, "color": 0x3498db}
                if i == 0:
                    img = work.get('images', {}).get('recommended_url')
                    if img: embed["image"] = {"url": img}
                    embed["description"] = "🏆 今期注目の作品"
                embeds.append(embed)
            
            return jsonify({
                'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE,
                'data': {'content': f"✅ **{datetime.now().year} {season} TOP10**", 'embeds': embeds}
            })

        elif cmd_name == 'service':
            work_name = options.get('work_name')
            works = get_anime_data(search_query=work_name, count=3)
            if not works:
                return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'content': "⚠️ 見つかりませんでした。"}})
            
            embeds = []
            for work in works:
                q = urllib.parse.quote(work['title'])
                links = f"[Google検索](https://www.google.com/search?q={q}+アニメ)"
                embeds.append({"title": work['title'], "url": work.get('official_site_url') or "", "description": links, "color": 0xe74c3c})
            return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'embeds': embeds}})

        elif cmd_name == 'yoso':
            threading.Thread(target=handle_yoso_prediction, args=(data.get('token'), APPLICATION_ID)).start()
            return jsonify({'type': InteractionResponseType.DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE})

    return jsonify({'type': InteractionResponseType.PONG})

monitor_thread = threading.Thread(target=background_monitor, daemon=True)
monitor_thread.start()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
