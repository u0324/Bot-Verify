import os
import requests
import urllib.parse
import threading
import time
import sqlite3
import numpy as np
import pandas as pd
from flask import Flask, jsonify, request
from datetime import datetime
import pytz 
from discord_interactions import verify_key, InteractionType, InteractionResponseType
from sklearn.ensemble import RandomForestRegressor

app = Flask(__name__)

# --- Secrets ---
DISCORD_PUBLIC_KEY = os.getenv('DISCORD_PUBLIC_KEY')
ANNICT_TOKEN = os.getenv('ANNICT_TOKEN')
APPLICATION_ID = os.getenv('APPLICATION_ID') 
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')

# --- 設定 ---
SEASON_MAP = {'spring': 'spring', 'summer': 'summer', 'fall': 'autumn', 'winter': 'winter'}
DB_PATH = 'stock_data.db'
timezone_jp = pytz.timezone('Asia/Tokyo')

# ==========================================
# 0. データベース操作 (永続化)
# ==========================================
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS history 
                        (timestamp TEXT, price REAL, month INTEGER, day INTEGER, hour INTEGER)''')
        conn.commit()

def save_price(price):
    now = datetime.now(timezone_jp)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT INTO history VALUES (?, ?, ?, ?, ?)",
                     (now.isoformat(), price, now.month, now.day, now.hour))
        conn.commit()

def load_history():
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query("SELECT * FROM history ORDER BY timestamp ASC", conn)
    return df

# ==========================================
# 1. 精密AIロジック (判定基準の厳格化)
# ==========================================
def analyze_logic():
    df = load_history()
    
    if len(df) < 7:
        return f"データ蓄積中... ({len(df)}/7)", 0, 50

    # 特徴量計算
    df['diff_1'] = df['price'].diff(1)
    ma5 = df['price'].rolling(window=5).mean()
    df['deviation'] = (df['price'] - ma5) / ma5 * 100
    df['momentum'] = df['price'] - df['price'].shift(3)

    train_df = df.dropna()
    features = ['month', 'day', 'hour', 'deviation', 'momentum']
    X = train_df[features].values
    y = train_df['price'].values

    # AIモデル
    model = RandomForestRegressor(n_estimators=100, max_depth=7, random_state=42)
    model.fit(X, y)
    
    now = datetime.now(timezone_jp)
    last_row = df.iloc[-1]
    current_features = np.array([[now.month, now.day, now.hour, last_row['deviation'], last_row['momentum']]])
    
    predicted_price = model.predict(current_features)[0]
    
    # RSI
    delta = df['price'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=min(len(df), 14)).mean().iloc[-1]
    loss = (-delta.where(delta < 0, 0)).rolling(window=min(len(df), 14)).mean().iloc[-1]
    rsi = 100.0 - (100.0 / (1.0 + (gain / loss))) if loss != 0 else 50.0

    current_price = df['price'].iloc[-1]
    diff = predicted_price - current_price

    # --- 指定通りの厳格判定ロジック ---
    if diff >= 10:
        status = "強力な上昇サイン 🚀"
    elif 1 <= diff <= 3:
        status = "緩やかな上昇見込み 📈"
    elif diff <= -10:
        status = "暴落注意・売り推奨 📉"
    elif -3 <= diff <= -1:
        status = "緩やかな下落見込み 📉"
    elif -1 < diff < 1:
        status = "安定・停滞相場 ➡️"
    else:
        status = "方向感の探り合い ➡️"

    return status, int(round(diff)), int(round(rsi))

# ==========================================
# 2. Discord機能 (非同期処理)
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

def handle_yoso_prediction(token, application_id, manual_price):
    save_price(float(manual_price))
    status, diff, rsi = analyze_logic()
    
    with sqlite3.connect(DB_PATH) as conn:
        count = conn.execute("SELECT COUNT(*) FROM history").fetchone()[0]

    embed = {
        "title": "💎 カカポ株価　AI診断",
        "description": f"最新価格 **{int(manual_price)}** を分析。月日・時間の法則を適用中。",
        "color": 0x5865F2,
        "fields": [
            {"name": "🤖 総合判定", "value": f"**{status}**", "inline": True},
            {"name": "🎯 次回予測価格", "value": f"{int(manual_price + diff)}", "inline": True},
            {"name": "🌡️ 市場熱感 (RSI)", "value": f"{rsi}%", "inline": True},
            {"name": "📈 変動幅予想", "value": f"{diff:+d}", "inline": True},
            {"name": "📊 学習データ数", "value": f"{count} 件", "inline": True}
        ],
        "footer": {"text": "時系列学習モデル：整数表示モード"}
    }
    url = f"https://discord.com/api/v10/webhooks/{application_id}/{token}/messages/@original"
    requests.patch(url, json={"embeds": [embed]})

# ==========================================
# 3. Flask & コマンド登録
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
            works = get_anime_data(season_key=options.get('season'))
            if not works: return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'content': "⚠️ データなし"}})
            embeds = [{"title": f"{i+1}. {work['title']}", "url": work.get('official_site_url'), "color": 0x3498db} for i, work in enumerate(works[:10])]
            return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'embeds': embeds}})

        elif cmd_name == 'service':
            works = get_anime_data(search_query=options.get('work_name'), count=3)
            if not works: return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'content': "⚠️ なし"}})
            embeds = [{"title": w['title'], "description": f"[Google](https://www.google.com/search?q={urllib.parse.quote(w['title'])}+アニメ)", "color": 0xe74c3c} for w in works]
            return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'embeds': embeds}})

        elif cmd_name == 'yoso':
            manual_price = options.get('price')
            threading.Thread(target=handle_yoso_prediction, args=(data.get('token'), APPLICATION_ID, manual_price)).start()
            return jsonify({'type': InteractionResponseType.DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE})

    return jsonify({'type': InteractionResponseType.PONG})

def register_commands():
    url = f"https://discord.com/api/v10/applications/{APPLICATION_ID}/commands"
    commands = [
        {"name": "yoso", "description": "精密株価予想", "options": [{"name": "price", "description": "現在の株価", "type": 4, "required": True}]},
        {"name": "anime", "description": "アニメ情報", "options": [{"name": "season", "description": "季節", "type": 3, "choices": [{"name":"春","value":"spring"},{"name":"夏","value":"summer"},{"name":"秋","value":"fall"},{"name":"冬","value":"winter"}]}]},
        {"name": "service", "description": "アニメ検索", "options": [{"name": "work_name", "description": "タイトル", "type": 3, "required": True}]}
    ]
    headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}
    time.sleep(5)
    for cmd in commands: requests.post(url, json=cmd, headers=headers)

if __name__ == '__main__':
    init_db()
    threading.Thread(target=register_commands).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
