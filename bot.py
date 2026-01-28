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
SEASON_MAP = {'spring': 'spring', 'summer': 'summer', 'fall': 'autumn', 'winter': 'winter'}
timezone_jp = pytz.timezone('Asia/Tokyo')

# ==========================================
# 0. データベース操作
# ==========================================
def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute('''CREATE TABLE IF NOT EXISTS history 
                       (timestamp TIMESTAMPTZ, price FLOAT, month INT, day INT, hour INT)''')
        cur.execute("ALTER TABLE history ADD COLUMN IF NOT EXISTS prediction_price FLOAT")
    conn.commit()
    conn.close()

def save_price(price, pred_price=None):
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
# 1. AIロジック
# ==========================================
def get_full_analysis():
    df = load_history()
    if len(df) < 10: 
        return f"蓄積中({len(df)}/10)", 0, 50, 0.0

    df = df.copy()
    df['ma5'] = df['price'].rolling(window=5, min_periods=1).mean()
    df['deviation'] = (df['price'] - df['ma5']) / df['ma5'] * 100
    df['momentum'] = df['price'].diff(3).fillna(0)

    features = ['month', 'day', 'hour', 'deviation', 'momentum']
    X = df[features].values
    y = df['price'].values

    try:
        model = RandomForestRegressor(n_estimators=100, max_depth=7, random_state=42)
        model.fit(X, y)
        
        now = datetime.now(timezone_jp)
        last_row = df.iloc[-1]
        current_features = np.array([[now.month, now.day, now.hour, last_row['deviation'], last_row['momentum']]])
        predicted_price_raw = model.predict(current_features)[0]
        
        delta = df['price'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=min(len(df), 14), min_periods=1).mean().iloc[-1]
        loss = (-delta.where(delta < 0, 0)).rolling(window=min(len(df), 14), min_periods=1).mean().iloc[-1]
        rsi = 100.0 - (100.0 / (1.0 + (gain / loss))) if loss != 0 else 50.0

        current_price = df['price'].iloc[-1]
        diff = int(round(predicted_price_raw - current_price))

        score = 0.0
        if diff >= 1: score += 1.0
        if rsi < 35: score += 1.5
        if rsi > 65: score -= 1.5

        if diff >= 5 or score >= 2.5: status = "強力な上昇サイン 🚀"
        elif diff >= 1: status = "緩やかな上昇見込み 📈"
        elif diff <= -5 or score <= -2.5: status = "下落注意 📉"
        else: status = "方向感の探り合い ➡️"

        return status, diff, int(round(rsi)), score
    except:
        return "AI調整中", 0, 50, 0.0

# ==========================================
# 2. Discord機能
# ==========================================
def handle_prediction_async(token, application_id, manual_price):
    status, diff, rsi, score = get_full_analysis()
    predicted_next = float(manual_price + diff)
    save_price(float(manual_price), predicted_next)
    
    count = len(load_history())
    embed = {
        "title": "🕊️ カカポ株価　AI診断",
        "description": f"最新価格 **{int(manual_price)}** を分析しました。",
        "color": 0x5865F2,
        "fields": [
            {"name": "🤖 総合判定", "value": f"**{status}**", "inline": False},
            {"name": "🎯 次回予測価格", "value": f"{int(predicted_next)}", "inline": True},
            {"name": "🌡️ RSI (熱感)", "value": f"{rsi}%", "inline": True},
            {"name": "📈 変動幅予想", "value": f"{diff:+d}", "inline": True},
            {"name": "📊 AIスコア", "value": f"{score:+.1f}", "inline": True},
            {"name": "📚 蓄積データ", "value": f"{count} 件", "inline": True}
        ],
        "footer": {"text": "AI学習式株価予測"}
    }
    url = f"https://discord.com/api/v10/webhooks/{application_id}/{token}/messages/@original"
    requests.patch(url, json={"embeds": [embed]})

def handle_show_data_async(token, application_id):
    df = load_history()
    if df.empty:
        content = "📚 データがまだありません。"
        embeds = []
    else:
        content = "" 
        lines = []
        display_df = df.iloc[::-1].head(10)

        # 1. まずデータをすべて lines に集める (forループ)
        for i, row in enumerate(display_df.itertuples()):
            ts = row.timestamp.astimezone(timezone_jp).strftime('%m/%d %H:%M')
            hit_mark = ""
            status_text = ""

            if i == 0:
                status_text = " (結果待ち)"
            else:
                if i + 1 < len(display_df):
                    prev_data = display_df.iloc[i+1]
                    p_price = getattr(prev_data, 'prediction_price', None)
                    if p_price is not None and not pd.isna(p_price):
                        try:
                            if abs(round(float(row.price)) - round(float(p_price))) <= 1:
                                hit_mark = " ✅"
                            else:
                                hit_mark = " ❌"
                        except:
                            hit_mark = ""

            lines.append(f"📁 {ts} | 価格: **{int(row.price)}**{hit_mark}{status_text}")

        # 2. すべて集め終わったら、1回だけ埋め込み(Embed)を作る
        # (ここを for と同じ列まで左にずらします)
        embeds = [{
            "title": "📚 最新10件の履歴と的中判定",
            "description": "\n".join(lines),
            "color": 0x2ecc71,
            "footer": {"text": "✅=的中 / ❌=外れ"}
        }]

    # 3. 最後に送信 (else と同じ列まで左にずらします)
    url = f"https://discord.com/api/v10/webhooks/{application_id}/{token}/messages/@original"
    requests.patch(url, json={"content": content, "embeds": embeds})


# --- アニメ検索機能 ---
def get_anime_data(search_query=None, season_key=None, count=10):
    url = "https://api.annict.com/v1/works"
    params = {'access_token': ANNICT_TOKEN, 'sort_watchers_count': 'desc', 'per_page': count}
    if search_query: params['filter_title'] = search_query
    elif season_key: params['filter_season'] = f"{datetime.now().year}-{SEASON_MAP.get(season_key, 'spring')}"
    try:
        res = requests.get(url, params=params, timeout=10).json()
        return res.get('works', [])
    except: return []

@app.route('/', methods=['POST'])
def interactions():
    signature = request.headers.get('X-Signature-Ed25519')
    timestamp = request.headers.get('X-Signature-Timestamp')
    if not signature or not timestamp or not verify_key(request.data, signature, timestamp, DISCORD_PUBLIC_KEY):
        return 'Unauthorized', 401

    data = request.json
    if data.get('type') == InteractionType.PING: return jsonify({'type': InteractionResponseType.PONG})

    user = data.get('member', {}).get('user', {}) or data.get('user', {})
    is_developer = (user.get('id') == YOUR_USER_ID)

    if data.get('type') == InteractionType.APPLICATION_COMMAND:
        cmd_name = data['data']['name']
        options = {opt['name']: opt['value'] for opt in data['data'].get('options', [])}

        if cmd_name in ['prediction', 'show_data', 'delete_latest']:
            if not is_developer: return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'content': "⚠️ 開発者専用", 'flags': 64}})
            
            if cmd_name == 'prediction':
                threading.Thread(target=handle_prediction_async, args=(data.get('token'), APPLICATION_ID, options.get('price'))).start()
                return jsonify({'type': InteractionResponseType.DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE})
            elif cmd_name == 'show_data':
                threading.Thread(target=handle_show_data_async, args=(data.get('token'), APPLICATION_ID)).start()
                return jsonify({'type': InteractionResponseType.DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE})
            elif cmd_name == 'delete_latest':
                conn = get_db_connection(); cur = conn.cursor()
                cur.execute("DELETE FROM history WHERE timestamp = (SELECT MAX(timestamp) FROM history)")
                cnt = cur.rowcount; conn.commit(); conn.close()
                return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'content': "✅ 削除成功" if cnt > 0 else "⚠️ データなし"}})

        elif cmd_name == 'anime':
            works = get_anime_data(season_key=options.get('season'))
            if not works: return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'content': "⚠️ データなし"}})
            embeds = [{"title": f"{i+1}. {work['title']}", "url": work.get('official_site_url'), "color": 0x3498db} for i, work in enumerate(works[:10])]
            return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'embeds': embeds}})

        elif cmd_name == 'service':
            works = get_anime_data(search_query=options.get('work_name'), count=3)
            if not works: return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'content': "⚠️ なし"}})
            embeds = [{"title": w['title'], "description": f"[Google検索](https://www.google.com/search?q={urllib.parse.quote(w['title'])}+アニメ)", "color": 0xe74c3c} for w in works]
            return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'embeds': embeds}})

    return jsonify({'type': InteractionResponseType.PONG})

def register_commands():
    base_url = f"https://discord.com/api/v10/applications/{APPLICATION_ID}/commands"
    headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}
    commands = [
        {"name": "prediction", "description": "カカポの株価を予測します", "options": [{"name": "price", "description": "価格", "type": 4, "required": True}]},
        {"name": "show_data", "description": "データの保存履歴と的中判定を表示します"},
        {"name": "delete_latest", "description": "最新のデータを一件削除します"},
        {"name": "anime", "description": "今期の人気アニメを表示します", "options": [{"name": "season", "description": "季節", "type": 3, "choices": [{"name":"春","value":"spring"},{"name":"夏","value":"summer"},{"name":"秋","value":"fall"},{"name":"冬","value":"winter"}]}]},
        {"name": "service", "description": "アニメを検索します", "options": [{"name": "work_name", "description": "作品名", "type": 3, "required": True}]}
    ]
    requests.put(base_url, json=commands, headers=headers)

if __name__ == '__main__':
    init_db()
    threading.Thread(target=register_commands).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
