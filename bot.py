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
# 精密分析用の機械学習ライブラリ
from sklearn.ensemble import RandomForestRegressor

app = Flask(__name__)

# --- Secrets ---
DISCORD_PUBLIC_KEY = os.getenv('DISCORD_PUBLIC_KEY')
ANNICT_TOKEN = os.getenv('ANNICT_TOKEN')
APPLICATION_ID = os.getenv('APPLICATION_ID') 
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')

# --- 設定 ---
SEASON_MAP = {'spring': 'spring', 'summer': 'summer', 'fall': 'autumn', 'winter': 'winter'}

# --- グローバル変数 ---
price_history = []
history_lock = threading.Lock()

# ==========================================
# 0. コマンド登録用関数 (起動時に自動実行)
# ==========================================
def register_commands():
    url = f"https://discord.com/api/v10/applications/{APPLICATION_ID}/commands"
    commands = [
        {
            "name": "yoso",
            "description": "カカポの株価予想をします",
            "options": [{"name": "price", "description": "現在の株価を入力", "type": 4, "required": True}]
        },
        {
            "name": "anime",
            "description": "アニメ情報を取得します",
            "options": [
                {
                    "name": "season",
                    "description": "季節を選択",
                    "type": 3,
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
            "options": [{"name": "work_name", "description": "タイトル", "type": 3, "required": True}]
        }
    ]
    headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}
    time.sleep(5)
    for cmd in commands:
        res = requests.post(url, json=cmd, headers=headers)
        if res.status_code in [200, 201]:
            print(f"✅ コマンド登録成功: /{cmd['name']}")

# ==========================================
# 1. 共通関数 (アニメ取得 & 精密AIロジック)
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
    # 本格分析には最低7データ必要
    if len(history) < 7:
        return f"データ蓄積中... (残り {7 - len(history)}件)", 0.0, 50.0

    df = pd.DataFrame(history, columns=['price'])
    
    # 【精度向上要素】
    df['diff_1'] = df['price'].diff(1)  # 前回の差
    ma5 = df['price'].rolling(window=5).mean()
    df['deviation'] = (df['price'] - ma5) / ma5 * 100  # 移動平均乖離率
    df['momentum'] = df['price'] - df['price'].shift(3)  # モメンタム

    train_df = df.dropna()
    X = np.array(range(len(train_df))).reshape(-1, 1)
    y = train_df['price'].values

    # ランダムフォレストによる多角予測
    model = RandomForestRegressor(n_estimators=50, max_depth=5, random_state=42)
    model.fit(X, y)
    predicted_price = model.predict(np.array([[len(df)]]))[0]
    
    # RSI計算
    delta = df['price'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=7).mean().iloc[-1]
    loss = (-delta.where(delta < 0, 0)).rolling(window=7).mean().iloc[-1]
    rsi = 100.0 - (100.0 / (1.0 + (gain / loss))) if loss != 0 else 50.0

    current_price = history[-1]
    diff = predicted_price - current_price
    volatility = np.std(history[-5:])

    # 判定スコア
    score = 0
    if diff > 0.3: score += 1
    if diff < -0.3: score -= 1
    if rsi < 35: score += 1.5
    if rsi > 65: score -= 1.5
    if df['deviation'].iloc[-1] < -2: score += 1
    if df['deviation'].iloc[-1] > 2: score -= 1

    if volatility < 0.1 and abs(diff) < 0.1: status = "安定・レンジ相場 ➡️"
    elif score >= 2: status = "強力な上昇サイン 🚀"
    elif score >= 0.5: status = "緩やかな上昇見込み 📈"
    elif score <= -2: status = "暴落注意・売り推奨 📉"
    elif score <= -0.5: status = "緩やかな下落見込み 📉"
    else: status = "方向感の探り合い ➡️"

    return status, diff, rsi

# ==========================================
# 2. 非同期レスポンス処理
# ==========================================

def handle_yoso_prediction(token, application_id, manual_price):
    with history_lock:
        price_history.append(float(manual_price))
        if len(price_history) > 100: price_history.pop(0)
        current_history = list(price_history)

    status, diff, rsi = analyze_logic(current_history)
    
    embed = {
        "title": "💎 カカポ株価　AI診断",
        "description": f"現在価格 **{manual_price:,.1f}** を分析しました。",
        "color": 0x5865F2,
        "fields": [
            {"name": "🤖 総合判定", "value": f"**{status}**", "inline": True},
            {"name": "🎯 次回予測価格", "value": f"{manual_price + diff:,.2f} コイン", "inline": True},
            {"name": "🌡️ 市場熱感 (RSI)", "value": f"{rsi:.1f}%", "inline": True},
            {"name": "📈 変動幅予想", "value": f"{diff:+.2f}", "inline": True},
            {"name": "📊 蓄積データ数", "value": f"{len(current_history)} 件", "inline": True}
        ],
        "footer": {"text": "RandomForest + 移動平均乖離率ロジック搭載"}
    }
    # 保留メッセージを最終結果に上書き
    url = f"https://discord.com/api/v10/webhooks/{application_id}/{token}/messages/@original"
    requests.patch(url, json={"embeds": [embed]})

# ==========================================
# 3. Flask Endpoint (Interaction)
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

        # --- アニメ検索機能 (復活・維持) ---
        if cmd_name == 'anime':
            works = get_anime_data(season_key=options.get('season'))
            if not works: return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'content': "⚠️ データなし"}})
            embeds = [{"title": f"{i+1}. {work['title']}", "url": work.get('official_site_url') or f"https://annict.com/works/{work['id']}", "color": 0x3498db} for i, work in enumerate(works[:10])]
            return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'embeds': embeds}})

        elif cmd_name == 'service':
            works = get_anime_data(search_query=options.get('work_name'), count=3)
            if not works: return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'content': "⚠️ なし"}})
            embeds = [{"title": w['title'], "description": f"[Google](https://www.google.com/search?q={urllib.parse.quote(w['title'])}+アニメ)", "color": 0xe74c3c} for w in works]
            return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'embeds': embeds}})

        # --- 精密AI予想機能 (タイムアウト対策版) ---
        elif cmd_name == 'yoso':
            manual_price = options.get('price')
            threading.Thread(target=handle_yoso_prediction, args=(data.get('token'), APPLICATION_ID, manual_price)).start()
            return jsonify({'type': InteractionResponseType.DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE})

    return jsonify({'type': InteractionResponseType.PONG})

if __name__ == '__main__':
    threading.Thread(target=register_commands).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
