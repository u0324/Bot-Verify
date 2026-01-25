import requests
import os

# --- 設定 (環境変数を直接入れるか、os.getenvで取得) ---
TOKEN = "MTQ0MDM3ODA2NjIyNzU2NDcyOQ.G4njp8.rjd8n10xRXn1IZnwm1nVSayjZdzoC73PwIg3MY"  # Botのトークンを入れてください
APP_ID = "1440378066227564729"  # アプリケーションIDを入れてください
GUILD_ID = "1421708178113953814"  # サーバーIDを入れると反映が爆速になります

url = f"https://discord.com/api/v10/applications/{APP_ID}/guilds/{GUILD_ID}/commands"

# /yoso コマンドの定義（priceオプション付き）
new_command = {
    "name": "yoso",
    "description": "今の株価を教えてAI予想を実行します",
    "options": [
        {
            "name": "price",
            "description": "現在の株価を入力してください",
            "type": 10, # 10 は Number (小数対応)
            "required": True
        }
    ]
}

headers = {"Authorization": f"Bot {TOKEN}"}

# 実行
response = requests.post(url, json=new_command, headers=headers)

if response.status_code in [200, 201]:
    print("✅ 成功！Discordに『price』枠付きの /yoso を登録しました。")
else:
    print(f"❌ 失敗: {response.status_code}")
    print(response.text)
