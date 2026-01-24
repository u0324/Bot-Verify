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

# --- Secrets (環境変数から取得) ---
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
    """Annict APIからアニメ情報を取得"""
    url = "https://api.annict.com/v1/works"
    params = {
        'access_token': ANNICT_TOKEN,
        'sort_watchers_count': 'desc',
        'per_page': count
    }
    
    if search_query:
        params['filter_title'] = search_query
    elif season_key:
        # SEASON_MAPから変換し、現在の西暦と結合
        annict_season = SEASON_MAP.get(season_key, 'spring')
        params['filter_season'] = f"{datetime.now().year}-{annict_season}"
    
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        return response.json().get('works', [])
    except Exception as e:
        print(f"Annict API Error: {e}")
        return []

def fetch_stock_price_sync():
    """外部サイトから株価をスクレイピング"""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        response = requests.get(TARGET_URL, headers=headers, timeout=10)
        content = response.text
        
        # HTMLタグを除去してテキストのみにする
        clean_text = re.sub(r'<[^>]+>', ' ', content)
        # 「数字 + コイン」のパターンを抽出
        match = re.search(r'([\d,.]+)\s*(?:コイン|coin|Coin)', clean_text)
        
        if match:
            price_str = match.group(1).replace(',', '')
            return float(price_str)
        else:
            print(f"Debug: 株価パターンが見つかりませんでした。テキスト先頭: {clean_text[:100]}")
    except Exception as e:
        print(f"Fetch Stock Error: {e}")
    return None

def analyze_logic(history):
    """テクニカル分析 (MA, RSI, 回帰分析)"""
    if len(history) < 3:
        return "データ蓄積中...", 0.0, 50.0

    df = pd.DataFrame(history, columns=['price'])
    
    # 1. 移動平均 (MA) シグナル
    ma = df['price'].rolling(window=min(len(df), 5)).mean().iloc[-1]
    ma_sig = 1 if df['price'].iloc[-1] > ma else -1

    # 2. RSI (相対力指数)
    delta = df['price'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=min(len(df), 10)).mean().iloc[-1]
    loss = (-delta.where(delta < 0, 0)).rolling(window=min(len(df), 10)).mean().iloc[-1]
    
    if loss == 0 or pd.isna(loss):
        rsi = 100.0 if gain > 0 else 50.0
    else:
        rsi = 100.0 - (100.0 / (1.0 + (gain / loss)))
    
    rsi_sig = -1 if rsi > 70 else (1 if rsi < 30 else 0)

    # 3. 線形回帰 (ML予測)
    X = np.array(range(len(df))).reshape(-1, 1)
    y = df['price'].values
    model = LinearRegression().fit(X, y)
    predicted = model.predict([[len(df)]])[0]
    ml_sig = 1 if predicted > df['price'].iloc[-1] else -1

    # 総合判定
    score = ma_sig + rsi_sig + ml_sig
    diff = predicted - df['price'].iloc[-1]

    if score >= 2: status = "上昇トレンド (買い) 🚀"
    elif score == 1: status = "やや上昇 📈"
    elif score <= -2: status = "下落トレンド (売り) 📉"
    elif score == -1: status = "やや下落 📉"
    else: status = "横ばい・様子見 ➡️"

    return status, diff, rsi

# ==========================================
# 2. バックグラウンド処理 & Webhook
# ==========================================

def background_monitor():
    """5分おきに自動で株価をチェックして履歴に保存"""
    print("✅ 株価監視スレッドを起動しました")
    while True:
        price = fetch_stock_price_sync()
        if price:
            with history_lock:
                price_history.append(price)
                if len(price_history) > 100:
                    price_history.pop(0)
            print(f"Monitor update: {price}")
        time.sleep(300)

def handle_yoso_prediction(token, application_id):
    """「yoso」コマンドの非同期処理と結果送信"""
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
            "footer": {"text": f"Updated at {datetime.now().strftime('%H:%M:%S')}"}
        }
        json_data = {"embeds": [embed]}
    else:
        json_data = {"content": "⚠️ 株価データの取得に失敗しました。サイトがダウンしている可能性があります。"}

    # DiscordのWebhook URL（Interactionに対する後追い返信）
    url = f"https://discord.com/api/v10/webhooks/{application_id}/{token}/messages/@original"
    requests.patch(url, json=json_data)

# ==========================================
# 3. Flask Endpoint (Discord Interactions)
# ==========================================

@app.route('/', methods=['POST'])
def interactions():
    # 署名検証
    signature = request.headers.get('X-Signature-Ed25519')
    timestamp = request.headers.get('X-Signature-Timestamp')
    if not signature or not timestamp or not verify_key(request.data, signature, timestamp, DISCORD_PUBLIC_KEY):
        return 'Unauthorized', 401

    data = request.json
    
    # PING (Discordからの接続テスト)
    if data.get('type') == InteractionType.PING:
        return jsonify({'type': InteractionResponseType.PONG})

    # コマンド実行
    if data.get('type') == InteractionType.APPLICATION_COMMAND:
        cmd_name = data['data']['name']
        options = {opt['name']: opt['value'] for opt in data['data'].get('options', [])}

        if cmd_name == 'anime':
            season = options.get('season')
            works = get_anime_data(season_key=season)
            if not works:
                return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'content': "⚠️ 今期のアニメデータが見つかりませんでした。"}})
            
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
                return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'content': f"⚠️ 「{work_name}」は見つかりませんでした。"}})
            
            embeds = []
            for work in works:
                q = urllib.parse.quote(work['title'])
                links = f"[Google検索](https://www.google.com/search?q={q}+アニメ)"
                embeds.append({
                    "title": work['title'], 
                    "url": work.get('official_site_url') or "", 
                    "description": links, 
                    "color": 0xe74c3c
                })
            return jsonify({
                'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE,
                'data': {'embeds': embeds}
            })

        elif cmd_name == 'yoso':
            # 処理に時間がかかるため、まず「考え中...」を返す
            token = data.get('token')
            threading.Thread(target=handle_yoso_prediction, args=(token, APPLICATION_ID)).start()
            return jsonify({'type': InteractionResponseType.DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE})

    return jsonify({'type': InteractionResponseType.PONG})

# ==========================================
# 4. 起動設定
# ==========================================

# 監視用スレッドの開始
monitor_thread = threading.Thread(target=background_monitor, daemon=True)
monitor_thread.start()

if __name__ == '__main__':
    # ポート番号は環境変数PORTから取得（デフォルト8080）
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
