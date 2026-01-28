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
# 0. データベース操作 (列不足エラーを自動修正)
# ==========================================
def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_db_connection()
    with conn.cursor() as cur:
        # 基本テーブル作成
        cur.execute('''CREATE TABLE IF NOT EXISTS history 
                       (timestamp TIMESTAMPTZ, price FLOAT, month INT, day INT, hour INT)''')
        # ログの UndefinedColumn エラーを解消する「予測値保存列」の追加
        cur.execute("ALTER TABLE history ADD COLUMN IF NOT EXISTS prediction_price FLOAT")
    conn.commit()
    conn.close()

def save_price(price, pred_price=None):
    now = datetime.now(timezone_jp)
    conn = get_db_connection()
    with conn.cursor() as cur:
        # 今回の価格と一緒に「次回への予言(pred_price)」も保存する
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
# 1. AIロジック (0 sampleクラッシュを確実に回避)
# ==========================================
def get_full_analysis():
    df = load_history()
    # ログの ValueError (0 samples) 回避: 最低限必要な件数を10件に設定
    if len(df) < 10: 
        return f"蓄積中({len(df)}/10)", 0, 50, 0.0

    df = df.copy()
    # 特徴量計算 (dropnaでデータが消えすぎないよう計算方法を安定化)
    df['ma5'] = df['price'].rolling(window=5, min_periods=1).mean()
    df['deviation'] = (df['price'] - df['ma5']) / df['ma5'] * 100
    df['momentum'] = df['price'].diff(3).fillna(0)

    train_df = df.copy()
    features = ['month', 'day', 'hour', 'deviation', 'momentum']
    X = train_df[features].values
    y = train_df['price'].values

    try:
        model = RandomForestRegressor(n_estimators=100, max_depth=7, random_state=42)
        model.fit(X, y)
        
        now = datetime.now(timezone_jp)
        last_row = df.iloc[-1]
        current_features = np.array([[now.month, now.day, now.hour, last_row['deviation'], last_row['momentum']]])
        predicted_price_raw = model.predict(current_features)[0]
        
        # RSI計算
        delta = df['price'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=min(len(df), 14), min_periods=1).mean().iloc[-1]
        loss = (-delta.where(delta < 0, 0)).rolling(window=min(len(df), 14), min_periods=1).mean().iloc[-1]
        rsi = 100.0 - (100.0 / (1.0 + (gain / loss))) if loss != 0 else 50.0

        current_price = df['price'].iloc[-1]
        diff = int(round(predicted_price_raw - current_price))

        # スコア判定
        score = 0.0
        if diff >= 1: score += 1.0
        if rsi < 35: score += 1.5
        if rsi > 65: score -= 1.5

        if diff >= 5 or score >= 2.5: status = "強力な上昇サイン 🚀"
        elif diff >= 1: status = "緩やかな上昇見込み 📈"
        elif diff <= -5 or score <= -2.5: status = "下落注意 📉"
        else: status = "方向感の探り合い ➡️"

        return status, diff, int(round(rsi)), score
    except Exception as e:
        print(f"AI Error: {e}")
        return "AI調整中", 0, 50, 0.0

# ==========================================
# 2. Discord機能 (全機能維持 ＋ 的中判定の正常化)
# ==========================================
def handle_prediction_async(token, application_id, manual_price):
    status, diff, rsi, score = get_full_analysis()
    # 答え合わせ用に「今回の予言」を計算してDBに保存
    predicted_next = float(manual_price + diff)
    save_price(float(manual_price), predicted_next)
    
    df_current = load_history()
    count = len(df_current)

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
        content = "📚 **最新10件の履歴と的中判定**"
        lines = []
        display_df = df.iloc[::-1].head(10) # 最新順
        
        for i, row in enumerate(display_df.itertuples()):
            ts = row.timestamp.astimezone(timezone_jp).strftime('%m/%d %H:%M')
            hit_mark = ""
            status_text = ""
            
            if i == 0:
                status_text = " (結果待ち)"
            else:
                # ひとつ過去のデータに保存されていた「予言」を取得
                # display_dfは逆順なので、i+1番目が「前回の予測時」のデータ
                if i + 1 < len(display_df):
                    prev_data = display_df.iloc[i+1]
                    if hasattr(prev_data, 'prediction_price') and prev_data.prediction_price:
                        # 実際の価格(row.price) と 予言(prev_data.prediction_price) を比較
                        if abs(row.price - prev_data.prediction_price) <= 1:
                            hit_mark = " ✅"
                        else:
                            hit_mark = " ❌"

            lines.append(f"📁 {ts} | 価格: **{int(row.price)}**{hit_mark}{status_text}")
        
        embeds = [{"title": "データ履歴", "description": "\n".join(lines), "color": 0x2ecc71, "footer": {"text": "✅=的中 / ❌=外れ"}}]

    url = f"https://discord.com/api/v10/webhooks/{application_id}/{token}/messages/@original"
    requests.patch(url, json={"content": content, "embeds": embeds})

# --- アニメ検索機能 (維持) ---
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
            if not is_developer: return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'content': "⚠️ 開発者専用コマンドです", 'flags': 64}})
            
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
                return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'content': "✅ 最新のデータを1件削除しました" if cnt > 0 else "⚠️ データが存在しません"}})

        elif cmd_name == 'anime':
            works = get_anime_data(season_key=options.get('season'))
            if not works: return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'content': "⚠️ アニメ情報が見つかりませんでした"}})
            embeds = [{"title": f"{i+1}. {work['title']}", "url": work.get('official_site_url'), "color": 0x3498db} for i, work in enumerate(works[:10])]
            return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'embeds': embeds}})

        elif cmd_name == 'service':
            works = get_anime_data(search_query=options.get('work_name'), count=3)
            if not works: return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'content': "⚠️ 作品が見つかりませんでした"}})
            embeds = [{"title": w['title'], "description": f"[Google検索](https://www.google.com/search?q={urllib.parse.quote(w['title'])}+アニメ)", "color": 0xe74c3c} for w in works]
            return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'embeds': embeds}})

    return jsonify({'type': InteractionResponseType.PONG})

def register_commands():
    base_url = f"https://discord.com/api/v10/applications/{APPLICATION_ID}/commands"
    headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}
    commands = [
        {"name": "prediction", "description": "カカポの株価を予測します", "options": [{"name": "price", "description": "現在の価格", "type": 4, "required": True}]},
        {"name": "show_data", "description": "履歴と的中判定を表示します"},
        {"name": "delete_latest", "description": "最新の履歴を削除します"},
        {"name": "anime", "description": "今期の人気アニメを表示", "options": [{"name": "season", "description": "季節", "type": 3, "choices": [{"name":"春","value":"spring"},{"name":"夏","value":"summer"},{"name":"秋","value":"fall"},{"name":"冬","value":"winter"}]}]},
        {"name": "service", "description": "アニメを検索します", "options": [{"name": "work_name", "description": "作品名", "type": 3, "required": True}]}
    ]
    requests.put(base_url, json=commands, headers=headers)

if __name__ == '__main__':
    init_db()
    threading.Thread(target=register_commands).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
