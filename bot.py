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
# 1. 精密AIロジック (全機能維持)
# ==========================================
def analyze_logic():
    df = load_history()
    if len(df) < 7:
        return f"蓄積中({len(df)}/7)", 0, 50, 0.0

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
    diff = predicted_price - current_price

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

    return status, int(round(diff)), int(round(rsi)), score

# ==========================================
# 2. Discord機能
# ==========================================
def handle_prediction_async(token, application_id, manual_price):
    save_price(float(manual_price))
    status, diff, rsi, score = analyze_logic()
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
    # 選択肢を更新するためにコマンドを再登録
    register_commands()

def handle_show_data_async(token, application_id):
    conn = get_db_connection()
    with conn.cursor(cursor_factory=DictCursor) as cur:
        cur.execute("SELECT timestamp, price FROM history ORDER BY timestamp DESC LIMIT 5")
        rows = cur.fetchall()
    conn.close()

    if not rows:
        content = "📚 まだ蓄積されたデータはありません。"
        embeds = []
    else:
        content = "📚 **最新5件の蓄積データ**"
        data_list = "".join([f"📅 {r['timestamp'].astimezone(timezone_jp).strftime('%m/%d %H:%M')} | 価格: **{int(r['price'])}**\n" for r in rows])
        embeds = [{"title": "データ履歴", "description": data_list, "color": 0x2ecc71, "footer": {"text": "AI学習用"}}]

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

# ==========================================
# 3. Flask & コマンド処理
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

    # --- 開発者（あなた）限定の判定 ---
    user = data.get('member', {}).get('user', {}) or data.get('user', {})
    sender_id = user.get('id')
    is_developer = (sender_id == YOUR_USER_ID)

    if data.get('type') == InteractionType.APPLICATION_COMMAND:
        cmd_name = data['data']['name']
        options = {opt['name']: opt['value'] for opt in data['data'].get('options', [])}

        # 開発者専用コマンド
        if cmd_name in ['prediction', 'show_data', 'delete_dup']:
            if not is_developer:
                return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'content': "⚠️ 開発者専用です", 'flags': 64}})
            
            if cmd_name == 'prediction':
                threading.Thread(target=handle_prediction_async, args=(data.get('token'), APPLICATION_ID, options.get('price'))).start()
                return jsonify({'type': InteractionResponseType.DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE})
            
            elif cmd_name == 'show_data':
                threading.Thread(target=handle_show_data_async, args=(data.get('token'), APPLICATION_ID)).start()
                return jsonify({'type': InteractionResponseType.DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE})

            elif cmd_name == 'delete_dup':
                target_ts = options.get('target')
                conn = get_db_connection()
                with conn.cursor() as cur:
                    # 秒単位の LIKE で確実に消す
                    cur.execute("DELETE FROM history WHERE timestamp::text LIKE %s", (f"{target_ts}%",))
                    deleted_count = cur.rowcount
                conn.commit()
                conn.close()
                # 削除後にコマンドの選択肢を最新にする
                register_commands()
                return jsonify({
                    'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE,
                    'data': {'content': f"✅ データを削除しました: `{target_ts}`" if deleted_count > 0 else "⚠️ 削除に失敗しました"}
                })

        # 公開コマンド
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
    
    # 最新5件をDBから取得して選択肢を作る
    delete_choices = []
    try:
        conn = get_db_connection()
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute("SELECT timestamp, price FROM history ORDER BY timestamp DESC LIMIT 5")
            rows = cur.fetchall()
        conn.close()
        for r in rows:
            ts_display = r['timestamp'].astimezone(timezone_jp).strftime('%m/%d %H:%M')
            ts_value = r['timestamp'].astimezone(timezone_jp).strftime('%Y-%m-%d %H:%M:%S')
            delete_choices.append({"name": f"{ts_display} (価格:{int(r['price'])})", "value": ts_value})
    except: pass

    # 選択肢がない場合のダミー
    if not delete_choices:
        delete_choices = [{"name": "データなし", "value": "none"}]

    commands = [
        {"name": "prediction", "description": "株価を予測し保存 (開発者専用)", "options": [{"name": "price", "description": "現在の株価", "type": 4, "required": True}]},
        {"name": "show_data", "description": "最新5件のデータを確認 (開発者専用)"},
        {
            "name": "delete_dup", 
            "description": "データを個別に削除 (開発者専用)",
            "options": [{
                "name": "target",
                "description": "削除するデータを選択",
                "type": 3,
                "required": True,
                "choices": delete_choices
            }]
        },
        {"name": "anime", "description": "今期のアニメ情報", "options": [{"name": "season", "description": "季節", "type": 3, "choices": [{"name":"春","value":"spring"},{"name":"夏","value":"summer"},{"name":"秋","value":"fall"},{"name":"冬","value":"winter"}]}]},
        {"name": "service", "description": "アニメを検索", "options": [{"name": "work_name", "description": "タイトル", "type": 3, "required": True}]}
    ]
    for cmd in commands: requests.post(base_url, json=cmd, headers=headers)

if __name__ == '__main__':
    init_db()
    # 初回起動時と、予測・削除のたびに register_commands が呼ばれるように設計
    threading.Thread(target=register_commands).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
