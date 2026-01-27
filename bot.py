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
    conn.commit()
    conn.close()

def save_price(price):
    now = datetime.now(timezone_jp)
    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("INSERT INTO history (timestamp, price, month, day, hour) VALUES (%s, %s, %s, %s, %s)",
                    (now, price, now.month, now.day, now.hour))
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
def analyze_logic(target_df=None):
    df = target_df if target_df is not None else load_history()
    if len(df) < 7: return "蓄積中"

    df = df.copy()
    df['diff_1'] = df['price'].diff(1)
    ma5 = df['price'].rolling(window=5).mean()
    df['deviation'] = (df['price'] - ma5) / ma5 * 100
    df['momentum'] = df['price'] - df['price'].shift(3)

    train_df = df.dropna()
    if len(train_df) < 2: return "蓄積中"

    features = ['month', 'day', 'hour', 'deviation', 'momentum']
    X = train_df[features].values
    y = train_df['price'].values

    model = RandomForestRegressor(n_estimators=100, max_depth=7, random_state=42)
    model.fit(X, y)
    
    last_row = df.iloc[-1]
    current_features = np.array([[last_row['month'], last_row['day'], last_row['hour'], last_row['deviation'], last_row['momentum']]])
    predicted_price = model.predict(current_features)[0]
    
    diff = int(round(predicted_price - last_row['price']))
    if diff >= 1: return "UP"
    elif diff <= -1: return "DOWN"
    else: return "STAY"

def get_full_analysis():
    df = load_history()
    if len(df) < 7: return f"蓄積中({len(df)}/7)", 0, 50, 0.0

    df['diff_1'] = df['price'].diff(1)
    ma5 = df['price'].rolling(window=5).mean()
    df['deviation'] = (df['price'] - ma5) / ma5 * 100
    df['momentum'] = df['price'] - df['price'].shift(3)

    train_df = df.dropna()
    features = ['month', 'day', 'hour', 'deviation', 'momentum']
    X = train_df[features].values
    y = train_df['price'].values

    model = RandomForestRegressor(n_estimators=100, max_depth=7, random_state=42)
    model.fit(X, y)
    
    now = datetime.now(timezone_jp)
    last_row = df.iloc[-1]
    current_features = np.array([[now.month, now.day, now.hour, last_row['deviation'], last_row['momentum']]])
    predicted_price = model.predict(current_features)[0]
    
    delta = df['price'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=min(len(df), 14)).mean().iloc[-1]
    loss = (-delta.where(delta < 0, 0)).rolling(window=min(len(df), 14)).mean().iloc[-1]
    rsi = 100.0 - (100.0 / (1.0 + (gain / loss))) if loss != 0 else 50.0

    current_price = df['price'].iloc[-1]
    diff = int(round(predicted_price - current_price))

    score = 0.0
    if diff >= 5: score += 2.0
    elif diff >= 1: score += 1.0
    if rsi < 30: score += 1.5
    if rsi > 70: score -= 1.5
    if last_row['deviation'] < -2: score += 1.0

    if diff >= 10 or score >= 3: status = "強力な上昇サイン 🚀"
    elif 1 <= diff <= 3 or score >= 1: status = "緩やかな上昇見込み 📈"
    elif diff <= -10 or score <= -3: status = "暴落注意 📉"
    elif -3 <= diff <= -1 or score <= -1: status = "緩やかな下落見込み 📉"
    else: status = "方向感の探り合い ➡️"

    return status, diff, int(round(rsi)), score

# ==========================================
# 2. Discord機能
# ==========================================
def handle_prediction_async(token, application_id, manual_price):
    save_price(float(manual_price))
    status, diff, rsi, score = get_full_analysis()
    df_current = load_history()
    count = len(df_current)

    embed = {
        "title": "🕊️ カカポ株価　AI診断",
        "description": f"最新価格 **{int(manual_price)}** を分析。",
        "color": 0x5865F2,
        "fields": [
            {"name": "🤖 総合判定", "value": f"**{status}**", "inline": False},
            {"name": "🎯 次回予測価格", "value": f"{int(manual_price + diff)}", "inline": True},
            {"name": "🌡️ RSI (熱感)", "value": f"{rsi}%", "inline": True},
            {"name": "📈 変動幅予想", "value": f"{diff:+d}", "inline": True},
            {"name": "📊 テクニカルスコア", "value": f"{score:+.1f}", "inline": True},
            {"name": "📚 蓄積データ数", "value": f"{count} 件", "inline": True}
        ],
        "footer": {"text": "AI学習式株価予測"}
    }
    url = f"https://discord.com/api/v10/webhooks/{application_id}/{token}/messages/@original"
    requests.patch(url, json={"embeds": [embed]})

def handle_show_data_async(token, application_id):
    df = load_history()
    if df.empty:
        content = "📚 まだ蓄積されたデータはありません。"
        embeds = []
    else:
        content = "📚 **最新10件の蓄積データと的中判定**"
        lines = []
        display_df = df.iloc[::-1].head(10)
        
        for i in range(len(display_df)):
            current_row = display_df.iloc[i]
            idx_in_full = df.index[df['timestamp'] == current_row['timestamp']][0]
            
            hit_mark = ""
            status_text = ""
            # 一番上（最新）は判定せず「結果待ち」にする
            if i == 0:
                if len(df) >= 7: status_text = " (次回の結果待ち)"
            elif idx_in_full > 0:
                prev_df = df.iloc[:idx_in_full]
                prediction = analyze_logic(prev_df)
                prev_price = df.iloc[idx_in_full - 1]['price']
                actual_price = current_row['price']
                
                if prediction == "UP" and actual_price > prev_price: hit_mark = " ✅"
                elif prediction == "DOWN" and actual_price < prev_price: hit_mark = " ✅"
                elif prediction == "STAY" and actual_price == prev_price: hit_mark = " ✅"
                elif prediction != "蓄積中": hit_mark = " ❌"

            ts = current_row['timestamp'].astimezone(timezone_jp).strftime('%m/%d %H:%M')
            lines.append(f"📅 {ts} | 価格: **{int(current_row['price'])}**{hit_mark}{status_text}")
        
        data_list = "\n".join(lines)
        embeds = [{"title": "データ履歴 (最新10件)", "description": data_list, "color": 0x2ecc71, "footer": {"text": "✅=的中 / ❌=外れ / 無印=学習前"}}]

    url = f"https://discord.com/api/v10/webhooks/{application_id}/{token}/messages/@original"
    requests.patch(url, json={"content": content, "embeds": embeds})

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
            if not is_developer: return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'content': "⚠️ 開発者専用です", 'flags': 64}})
            
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
                return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'content': "✅ 最新を削除しました" if cnt > 0 else "⚠️ データなし"}})

        elif cmd_name == 'anime':
            works = get_anime_data(season_key=options.get('season'))
            if not works: return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'content': "⚠️ データなし"}})
            embeds = [{"title": f"{i+1}. {work['title']}", "url": work.get('official_site_url'), "color": 0x3498db} for i, work in enumerate(works[:10])]
            return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'embeds': embeds}})

        elif cmd_name == 'service':
            works = get_anime_data(search_query=options.get('work_name'), count=3)
            if not works: return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'content': "⚠️ なし"}})
            embeds = [{"title": w['title'], "description": f"[Google](https://www.google.com/search?q={urllib.parse.quote(w['title'])}+アニメ)", "color": 0xe74c3c} for w in works]
            return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'embeds': embeds}})

    return jsonify({'type': InteractionResponseType.PONG})

def register_commands():
    base_url = f"https://discord.com/api/v10/applications/{APPLICATION_ID}/commands"
    headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}
    requests.put(base_url, json=[], headers=headers); time.sleep(2)
    commands = [
        {"name": "prediction", "description": "株価を予測・保存", "options": [{"name": "price", "description": "価格", "type": 4, "required": True}]},
        {"name": "show_data", "description": "履歴10件と的中判定を表示"},
        {"name": "delete_latest", "description": "最新1件を削除"},
        {"name": "anime", "description": "アニメ情報", "options": [{"name": "season", "description": "季節", "type": 3, "choices": [{"name":"春","value":"spring"},{"name":"夏","value":"summer"},{"name":"秋","value":"fall"},{"name":"冬","value":"winter"}]}]},
        {"name": "service", "description": "アニメ検索", "options": [{"name": "work_name", "description": "作品名", "type": 3, "required": True}]}
    ]
    requests.put(base_url, json=commands, headers=headers)

if __name__ == '__main__':
    init_db()
    threading.Thread(target=register_commands).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
  
