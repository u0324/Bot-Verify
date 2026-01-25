import os
import requests
import urllib.parse
import threading
import time
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
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN') # これが必要です！

# --- 設定 ---
SEASON_MAP = {'spring': 'spring', 'summer': 'summer', 'fall': 'autumn', 'winter': 'winter'}

# --- グローバル変数 ---
price_history = []
history_lock = threading.Lock()

# ==========================================
# 0. コマンド登録用関数 (起動時に自動実行)
# ==========================================
def register_commands():
    """Discord APIにコマンドを直接登録する"""
    url = f"https://discord.com/api/v10/applications/{APPLICATION_ID}/commands"
    
    commands = [
        {
            "name": "yoso",
            "description": "株価予想をします",
            "options": [
                {
                    "name": "price",
                    "description": "現在の株価を入力してください",
                    "type": 4,  # INTEGER
                    "required": True
                }
            ]
        },
        {
            "name": "anime",
            "description": "アニメ情報を取得します",
            "options": [
                {
                    "name": "season",
                    "description": "季節を選択してください",
                    "type": 3,  # STRING
                    "choices": [
                        {"name": "春", "value": "spring"},
                        {"name": "夏", "value": "summer"},
                        {"name": "秋", "value": "fall"},
                        {"name": "冬", "value": "winter"}
                    ]
                }
            ]
        },
        {
            "name": "service",
            "description": "アニメを検索します",
            "options": [
                {
                    "name": "work_name",
                    "description": "アニメのタイトル",
                    "type": 3,
                    "required": True
                }
            ]
        }
    ]

    headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}
    
    # 少し待ってから実行（サーバー起動との競合防止）
    time.sleep(5)
    for cmd in commands:
        response = requests.post(url, json=cmd, headers=headers)
        if response.status_code in [200, 201]:
            print(f"✅ コマンド登録成功: /{cmd['name']}")
        else:
            print(f"❌ コmンド登録失敗: /{cmd['name']} ({response.status_code})")

# ==========================================
# 1. ロジック / Webhook処理 (変更なし)
# ==========================================
def get_anime_data(search_query=None, season_key=None, count=10):
    url = "https://api.annict.com/v1/works"
    params = {'access_token': ANNICT_TOKEN, 'sort_watchers_count': 'desc', 'per_page': count}
    if search_query: params['filter_title'] = search_query
    elif season_key: params['filter_season'] = f"{datetime.now().year}-{SEASON_MAP.get(season_key, 'spring')}"
    try:
        res = requests.get(url, params=params, timeout=10).json()
        return res.get('works', [])
    except: return []

def analyze_logic(history):
    if len(history) < 3: return "データ蓄積中...", 0.0, 50.0
    df = pd.DataFrame(history, columns=['price'])
    ma = df['price'].rolling(window=min(len(df), 5)).mean().iloc[-1]
    ma_sig = 1 if df['price'].iloc[-1] > ma else -1
    delta = df['price'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=min(len(df), 10)).mean().iloc[-1]
    loss = (-delta.where(delta < 0, 0)).rolling(window=min(len(df), 10)).mean().iloc[-1]
    rsi = 100.0 - (100.0 / (1.0 + (gain / loss))) if loss != 0 and not pd.isna(loss) else 50.0
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

def handle_yoso_prediction_manual(token, application_id, manual_price):
    with history_lock:
        price_history.append(float(manual_price))
        if len(price_history) > 100: price_history.pop(0)
        current_history = list(price_history)
    status, diff, rsi = analyze_logic(current_history)
    embed = {
        "title": "📊 カカポ株価 AI予想",
        "description": f"あなたが入力した **{manual_price:,.1f} コイン** を元に分析しました。",
        "color": 0x00b0f4,
        "fields": [
            {"name": "🤖 AIの判断", "value": f"**{status}**", "inline": True},
            {"name": "🔮 次の予想変動", "value": f"{diff:+.2f} コイン", "inline": True},
            {"name": "🌡️ RSI", "value": f"{rsi:.1f}%", "inline": True},
            {"name": "📚 蓄積データ数", "value": f"{len(current_history)} 件", "inline": False}
        ]
    }
    url = f"https://discord.com/api/v10/webhooks/{application_id}/{token}/messages/@original"
    requests.patch(url, json={"embeds": [embed]})

# ==========================================
# 2. Flask Endpoint (Interaction)
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
            if not works: return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'content': "⚠️ データなし"}})
            embeds = [{"title": f"{i+1}. {work['title']}", "url": work.get('official_site_url') or f"https://annict.com/works/{work['id']}", "color": 0x3498db} for i, work in enumerate(works[:10])]
            return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'embeds': embeds}})

        elif cmd_name == 'service':
            work_name = options.get('work_name')
            works = get_anime_data(search_query=work_name, count=3)
            if not works: return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'content': "⚠️ なし"}})
            embeds = [{"title": w['title'], "description": f"[Google](https://www.google.com/search?q={urllib.parse.quote(w['title'])}+アニメ)", "color": 0xe74c3c} for w in works]
            return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'embeds': embeds}})

        elif cmd_name == 'yoso':
            manual_price = options.get('price')
            threading.Thread(target=handle_yoso_prediction_manual, args=(data.get('token'), APPLICATION_ID, manual_price)).start()
            return jsonify({'type': InteractionResponseType.DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE})

    return jsonify({'type': InteractionResponseType.PONG})

if __name__ == '__main__':
    # 登録用スレッドを開始
    threading.Thread(target=register_commands).start()
    
    port = int(os.environ.get("PORT", 8080)) 
    app.run(host='0.0.0.0', port=port)
