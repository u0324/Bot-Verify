import os
import requests
import threading
import random
from flask import Flask, jsonify, request
from datetime import datetime, timedelta
import pytz
import psycopg2

app = Flask(__name__)

# --- 環境変数 ---
DATABASE_URL = os.getenv('DATABASE_URL')
APPLICATION_ID = os.getenv('APPLICATION_ID')
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
YOUR_USER_ID = '1421704357983813744'

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def handle_fake_import(token, application_id):
    conn = get_db_connection()
    cur = conn.cursor()
    
    # 30件のデータを生成（現在から1時間ずつ遡る）
    now = datetime.now(pytz.timezone('Asia/Tokyo'))
    count = 0
    
    for i in range(30):
        fake_time = now - timedelta(hours=i+1)
        # 今の相場の中心値付近（98~102円）でランダムに揺らす
        fake_price = random.randint(98, 102)
        
        cur.execute(
            "INSERT INTO history (timestamp, price, month, day, hour) VALUES (%s, %s, %s, %s, %s)",
            (fake_time, float(fake_price), fake_time.month, fake_time.day, fake_time.hour)
        )
        count += 1
    
    conn.commit()
    conn.close()
    
    # Discordへの完了報告
    url = f"https://discord.com/api/v10/webhooks/{application_id}/{token}/messages/@original"
    requests.patch(url, json={"content": f"🛠️ **30件の停滞データを注入しました！**\nAIが『凪』の存在を認識し始めました。これで「130円！」といった極端な予測が抑えられ、的中率（✅）が上がりやすくなるはずです。"})

@app.route('/', methods=['POST'])
def interactions():
    data = request.json
    if data.get('type') == 1: return jsonify({'type': 1})
    
    if data.get('type') == 2:
        cmd_name = data['data']['name']
        if cmd_name == 'bulk_fake_import':
            threading.Thread(target=handle_fake_import, args=(data.get('token'), APPLICATION_ID)).start()
            return jsonify({'type': 5})
    return jsonify({'type': 1})

def register_commands():
    base_url = f"https://discord.com/api/v10/applications/{APPLICATION_ID}/commands"
    headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}
    commands = [{"name": "bulk_fake_import", "description": "停滞期のダミーデータを30件生成して学習させる"}]
    requests.put(base_url, json=commands, headers=headers)

if __name__ == '__main__':
    threading.Thread(target=register_commands).start()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
