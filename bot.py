import os
import requests
import urllib.parse
import threading
import time
import psycopg2 
from psycopg2.extras import DictCursor
import numpy as np
import pandas as pd
from flask import Flask, jsonify, request
from datetime import datetime
import pytz 
from discord_interactions import verify_key, InteractionType, InteractionResponseType
from sklearn.ensemble import RandomForestRegressor

app = Flask(__name__)

# --- Secrets ---
DATABASE_URL = os.getenv('DATABASE_URL') 
DISCORD_PUBLIC_KEY = os.getenv('DISCORD_PUBLIC_KEY')
ANNICT_TOKEN = os.getenv('ANNICT_TOKEN')
APPLICATION_ID = os.getenv('APPLICATION_ID') 
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
YOUR_USER_ID = '1421704357983813744'

# --- 設定 ---
SEASON_MAP = {'春': 'spring', '夏': 'summer', '秋': 'fall', '冬': 'winter'}
timezone_jp = pytz.timezone('Asia/Tokyo')

# ==========================================
# 0. データベース操作
# ==========================================
def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_db_connection()
    with conn.cursor() as cur:
        # prediction_priceカラム（AIの予言を保存する場所）を確実に作成
        cur.execute('''CREATE TABLE IF NOT EXISTS history 
                       (timestamp TIMESTAMPTZ, price FLOAT, month INT, day INT, hour INT, prediction_price FLOAT)''')
    conn.commit()
    conn.close()

def save_price(price, pred_price):
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
# 1. 精密AIロジック
# ==========================================
def get_full_analysis():
    df = load_history()
    if len(df) < 5: return "データ蓄積中...", 0, 50, 0.0

    df['diff_1'] = df['price'].diff(1)
    ma = df['price'].rolling(window=min(len(df), 5)).mean()
    df['deviation'] = (df['price'] - ma) / ma * 100
    df['momentum'] = df['price'] - df['price'].shift(min(len(df)-1, 3))
    train_df = df.dropna()

    if len(train_df) < 2: return "分析準備中...", 0, 50, 0.0

    features = ['month', 'day', 'hour', 'deviation', 'momentum']
    X = train_df[features].values
    y = train_df['price'].values

    model = RandomForestRegressor(n_estimators=100, max_depth=7, random_state=42)
    model.fit(X, y)
    
    now = datetime.now(timezone_jp)
    last_row = df.iloc[-1]
    current_features = np.array([[now.month, now.day, now.hour, last_row['deviation'], last_row['momentum']]])
    pred_raw = model.predict(current_features)[0]
    
    delta = df['price'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=min(len(df), 14)).mean().iloc[-1]
    loss = (-delta.where(delta < 0, 0)).rolling(window=min(len(df), 14)).mean().iloc[-1]
    rsi = 100.0 - (100.0 / (1.0 + (gain / loss))) if loss != 0 else 50.0

    diff = int(round(pred_raw - df.iloc[-1]['price']))
    
    score = 0.0
    if diff >= 5: score += 2.0
    if rsi < 40: score += 1.0
    if rsi > 70: score -= 1.0

    if diff >= 10: status = "強力な上昇サイン 🚀"
    elif 1 <= diff <= 3: status = "緩やかな上昇見込み 📈"
    elif diff <= -10: status = "暴落注意 📉"
    elif -3 <= diff <= -1: status = "緩やかな下落見込み 📉"
    else: status = "方向感の探り合い ➡️"

    return status, diff, int(round(rsi)), score

# ==========================================
# 2. Discord機能実装
# ==========================================
def handle_prediction_async(token, application_id, manual_price):
    status, diff, rsi, score = get_full_analysis()
    predicted_next = float(manual_price + diff)
    save_price(float(manual_price), predicted_next) # 予測値をDBに保存
    count = len(load_history())

    embed = {
        "title": "🕊️ カカポ株価　AI診断",
        "description": f"最新価格 **{int(manual_price)}** を分析完了。",
        "color": 0x5865F2,
        "fields": [
            {"name": "🤖 総合判定", "value": f"**{status}**", "inline": False},
            {"name": "🎯 次回予測価格", "value": f"**{int(predicted_next)}**", "inline": True},
            {"name": "🌡️ RSI (熱感)", "value": f"{rsi}%", "inline": True},
            {"name": "📈 変動幅予想", "value": f"{diff:+d}", "inline": True},
            {"name": "📊 テクニカルスコア", "value": f"{score:+.1f}", "inline": True},
            {"name": "📚 蓄積データ数", "value": f"{count} 件", "inline": True}
        ],
        "footer": {"text": "的中判定を強化しました (予言を記録中)"}
    }
    requests.patch(f"https://discord.com/api/v10/webhooks/{application_id}/{token}/messages/@original", json={"embeds": [embed]})

def handle_show_data_async(token, application_id):
    df = load_history()
    if df.empty:
        content = "📚 データがありません。"
    else:
        content = "📚 **最新10件のデータ履歴**"
        lines = []
        display_df = df.iloc[::-1].head(10)
        for i, row in enumerate(display_df.itertuples()):
            ts = row.timestamp.astimezone(timezone_jp).strftime('%m/%d %H:%M')
            idx = row.Index
            hit, pred_info = "", ""
            if idx > 0:
                prev_pred = df.iloc[idx-1]['prediction_price'] # 1つ前の入力時の予測値
                if prev_pred is not None:
                    pred_info = f" (予:{int(prev_pred)})"
                    # 予測と現在の価格の差が1以内なら ✅
                    hit = " ✅" if abs(row.price - prev_pred) <= 1 else " ❌"
            status_tag = " (待)" if i == 0 else ""
            lines.append(f"📁 {ts} | **{int(row.price)}**{pred_info}{hit}{status_tag}")
        embed = {"title": "的中判定 (前回の予測 vs 今回の実測)", "description": "\n".join(lines), "color": 0x2ecc71, "footer": {"text": "✅=予言的中 / ❌=外れ / (待)=結果待ち"}}
        requests.patch(f"https://discord.com/api/v10/webhooks/{application_id}/{token}/messages/@original", json={"content": content, "embeds": [embed]})

# ==========================================
# 3. アニメ情報 (Annict連携)
# ==========================================
def get_anime_data(season_key):
    url = "https://api.annict.com/v1/works"
    params = {'access_token': ANNICT_TOKEN, 'sort_watchers_count': 'desc', 'per_page': 10, 'filter_season': f"2026-{SEASON_MAP.get(season_key, 'spring')}"}
    try:
        res = requests.get(url, params=params, timeout=10).json()
        return res.get('works', [])
    except: return []

# ==========================================
# 4. Flask & Interactions
# ==========================================
@app.route('/', methods=['POST'])
def interactions():
    signature = request.headers.get('X-Signature-Ed25519')
    timestamp = request.headers.get('X-Signature-Timestamp')
    if not verify_key(request.data, signature, timestamp, DISCORD_PUBLIC_KEY): return 'Unauthorized', 401

    data = request.json
    if data.get('type') == 1: return jsonify({'type': 1})
    user_id = (data.get('member', {}).get('user', {}) or data.get('user', {})).get('id')
    is_dev = (user_id == YOUR_USER_ID)

    if data.get('type') == 2:
        cmd = data['data']['name']
        opts = {opt['name']: opt['value'] for opt in data['data'].get('options', [])}

        if cmd == 'prediction' and is_dev:
            threading.Thread(target=handle_prediction_async, args=(data.get('token'), APPLICATION_ID, opts['price'])).start()
            return jsonify({'type': 5})
        elif cmd == 'show_data' and is_dev:
            threading.Thread(target=handle_show_data_async, args=(data.get('token'), APPLICATION_ID)).start()
            return jsonify({'type': 5})
        elif cmd == 'delete_latest' and is_dev:
            conn = get_db_connection(); cur = conn.cursor()
            cur.execute("DELETE FROM history WHERE timestamp = (SELECT MAX(timestamp) FROM history)")
            cnt = cur.rowcount; conn.commit(); conn.close()
            return jsonify({'type': 4, 'data': {'content': "✅ 最新の履歴を1件削除しました" if cnt > 0 else "⚠️ 削除するデータがありません"}})
        elif cmd == 'anime':
            works = get_anime_data(opts.get('season'))
            embeds = [{"title": f"{i+1}. {w['title']}", "url": w.get('official_site_url'), "color": 0x3498db} for i, w in enumerate(works)]
            return jsonify({'type': 4, 'data': {'embeds': embeds if embeds else None, 'content': "⚠️ 今期のデータは見つかりませんでした" if not embeds else ""}})

    return jsonify({'type': 1})

def register_commands():
    base_url = f"https://discord.com/api/v10/applications/{APPLICATION_ID}/commands"
    headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}
    cmds = [
        {"name": "prediction", "description": "カカポの株価を予測します", "options": [{"name": "price", "description": "現在の価格", "type": 4, "required": True}]},
        {"name": "show_data", "description": "過去のデータ10件の履歴と的中判定を表示します"},
        {"name": "delete_latest", "description": "最新1件のデータを削除します"},
        {"name": "anime", "description": "今年の人気アニメを表示します", "options": [{"name": "season", "description": "季節", "type": 3, "required": True, "choices": [{"name":"春","value":"春"},{"name":"夏","value":"夏"},{"name":"秋","value":"秋"},{"name":"冬","value":"冬"}]}]}
    ]
    requests.put(base_url, json=cmds, headers=headers)

if __name__ == '__main__':
    init_db()
    threading.Thread(target=register_commands).start()
    app.run(host='0.0.0.0', port=8080)
