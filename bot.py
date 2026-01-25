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
# サイトへのアクセスは行わないため、URLは使用しません
SEASON_MAP = {'spring': 'spring', 'summer': 'summer', 'fall': 'autumn', 'winter': 'winter'}

# --- グローバル変数 ---
price_history = []
history_lock = threading.Lock()

# ==========================================
# 1. 共通関数 (アニメ・予測ロジック)
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

def analyze_logic(history):
    # 最低3つのデータがないと予測不可
    if len(history) < 3:
        return "データ蓄積中...", 0.0, 50.0

    df = pd.DataFrame(history, columns=['price'])
    
    # 1. 移動平均 (MA)
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
# 2. Webhook処理 (手動入力後の非同期返信)
# ==========================================

def handle_yoso_prediction_manual(token, application_id, manual_price):
    # 入力された価格を履歴に追加
    with history_lock:
        price_history.append(float(manual_price))
        if len(price_history) > 100: price_history.pop(0)
        current_history = list(price_history)

    # 予測実行
    status, diff, rsi = analyze_logic(current_history)
    
    embed = {
        "title": "📊 カカポ株価 AI予想 (手動入力モード)",
        "description": f"あなたが入力した **{manual_price:,.1f} コイン** を元に分析しました。",
        "color": 0x00b0f4,
        "fields": [
            {"name": "🤖 AIの判断", "value": f"**{status}**", "inline": True},
            {"name": "🔮 次の予想変動", "value": f"{diff:+.2f} コイン", "inline": True},
            {"name": "🌡️ RSI", "value": f"{rsi:.1f}%", "inline": True},
            {"name": "📚 蓄積データ数", "value": f"{len(current_history)} 件", "inline": False}
        ],
        "footer": {"text": "3件以上の入力で正確なグラフ予測が始まります"}
    }
    
    url = f"https://discord.com/api/v10/webhooks/{application_id}/{token}/messages/@original"
    requests.patch(url, json={"embeds": [embed]})

# ==========================================
# 3. Flask Endpoint (Discord Interactions)
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

        # --- 1. /anime ---
        if cmd_name == 'anime':
            season = options.get('season')
            works = get_anime_data(season_key=season)
            if not works: return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'content': "⚠️ データなし"}})
            embeds = []
            for i, work in enumerate(works[:10]):
                url = work.get('official_site_url') or f"https://annict.com/works/{work['id']}"
                embed = {"title": f"{i+1}. {work['title']}", "url": url, "color": 0x3498db}
                embeds.append(embed)
            return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'embeds': embeds}})

        # --- 2. /service ---
        elif cmd_name == 'service':
            work_name = options.get('work_name')
            works = get_anime_data(search_query=work_name, count=3)
            if not works: return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'content': "⚠️ なし"}})
            embeds = [{"title": w['title'], "description": f"[Google](https://www.google.com/search?q={urllib.parse.quote(w['title'])}+アニメ)", "color": 0xe74c3c} for w in works]
            return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'embeds': embeds}})

        # --- 3. /yoso (手動入力版) ---
        elif cmd_name == 'yoso':
            user_id = data.get('member', {}).get('user', {}).get('id') or data.get('user', {}).get('id')
            if user_id != '1421704357983813744':
                return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'content': "⚠️ 管理者専用です。"}})
            
            manual_price = options.get('price')
            if manual_price is None:
                return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'content': "⚠️ `/yoso price:現在の価格` を入力してください。"}})
            
            # 非同期で予測計算を開始
            threading.Thread(target=handle_yoso_prediction_manual, args=(data.get('token'), APPLICATION_ID, manual_price)).start()
            return jsonify({'type': InteractionResponseType.DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE})

    return jsonify({'type': InteractionResponseType.PONG})

if __name__ == '__main__':
    # サイト取得を行わないため、バックグラウンドスレッド(background_monitor)は削除しました
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
