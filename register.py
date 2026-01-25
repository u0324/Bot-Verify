import requests
import os

# --- ZeaburのVariablesから取得 ---
TOKEN = os.getenv('DISCORD_BOT_TOKEN')
APP_ID = os.getenv('APPLICATION_ID')
GUILD_ID = '1421708178125422612'  # あなたのサーバーID

if not TOKEN or not APP_ID:
    print("❌ エラー: ZeaburのVariablesに 'DISCORD_BOT_TOKEN' または 'APPLICATION_ID' が設定されていません。")
else:
    url = f"https://discord.com/api/v10/applications/{APP_ID}/guilds/{GUILD_ID}/commands"
    headers = {"Authorization": f"Bot {TOKEN}"}

    # コマンドの定義
    commands = [
        {
            "name": "yoso",
            "description": "今の株価を教えてAI予想を実行します",
            "options": [
                {
                    "name": "price",
                    "description": "現在の株価を入力してください",
                    "type": 10,  # 10 は Number
                    "required": True
                }
            ]
        },
        {
            "name": "anime",
            "description": "今期の人気アニメを取得します"
            # オプションなし（元のまま）
        },
        {
            "name": "service",
            "description": "アニメの配信サイトを検索します",
            "options": [
                {
                    "name": "work_name",
                    "description": "アニメのタイトルを入力",
                    "type": 3,  # String
                    "required": True
                }
            ]
        }
    ]

    # 一括登録（上書き）
    for cmd in commands:
        res = requests.post(url, json=cmd, headers=headers)
        if res.status_code in [200, 201]:
            print(f"✅ 登録成功: /{cmd['name']}")
        else:
            print(f"❌ 失敗: /{cmd['name']} ({res.status_code})")
