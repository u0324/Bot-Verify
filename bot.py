import os
import requests
import threading
import psycopg2
from flask import Flask, jsonify, request

app = Flask(__name__)

# --- 環境変数 ---
DATABASE_URL = os.getenv('DATABASE_URL')
APPLICATION_ID = os.getenv('APPLICATION_ID')
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def handle_clean_data(token, application_id):
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 1. 2026年1月1日より前の古いデータをすべて削除
    cur.execute("DELETE FROM history WHERE timestamp < '2026-01-01 00:00:00'")
    
    # 2. ついでに先ほどのダミーデータ（12月以前扱いになっている場合）も確実に消去
    # ※timestampが1月以降のダミーデータは残ります。
    
    conn.commit()
    
    # 現在の残り件数を確認
    cur.execute("SELECT COUNT(*) FROM history")
    count = cur.fetchone()[0]
    
    cur.close()
    conn.close()
    
    # 完了報告
    url = f"https://discord.com/api/v10/webhooks/{application_id}/{token}/messages/@original"
    requests.patch(url, json={"content": f"🧹 **データベースの掃除が完了しました！**\n1月以前の古い記憶をすべて消去しました。現在の有効データ数は **{count}件** です。\n\nこれでAIの『高騰バイアス』が消えたので、前の【完全版コード】に戻して運用を再開してください！"})

@app.route('/', methods=['POST'])
def interactions():
    data = request.json
    if data.get('type') == 1: return jsonify({'type': 1})
    
    if data.get('type') == 2:
        if data['data']['name'] == 'clean_and_update':
            threading.Thread(target=handle_clean_data, args=(data.get('token'), APPLICATION_ID)).start()
            return jsonify({'type': 5})
    return jsonify({'type': 1})

def register_commands():
    base_url = f"https://discord.com/api/v10/applications/{APPLICATION_ID}/commands"
    headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}
    commands = [{"name": "clean_and_update", "description": "1月以前のデータを削除してAIをリセットする"}]
    requests.put(base_url, json=commands, headers=headers)

if __name__ == '__main__':
    threading.Thread(target=register_commands).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
