import os
import requests
import urllib.parse
from flask import Flask, jsonify, request
from datetime import datetime
from discord_interactions import verify_key, InteractionType, InteractionResponseType

app = Flask(__name__)

# --- Secrets (ZeaburのVariablesで設定するもの) ---
DISCORD_PUBLIC_KEY = os.getenv('DISCORD_PUBLIC_KEY')
ANNICT_TOKEN = os.getenv('ANNICT_TOKEN')

SEASON_MAP = {'spring': 'spring', 'summer': 'summer', 'fall': 'autumn', 'winter': 'winter'}

def get_anime_data(search_query=None, season_key=None, count=10):
    url = "https://api.annict.com/v1/works"
    params = {'access_token': ANNICT_TOKEN, 'sort_watchers_count': 'desc', 'per_page': count}
    if search_query:
        params['filter_title'] = search_query
    elif season_key:
        params['filter_season'] = f"{datetime.now().year}-{SEASON_MAP[season_key]}"
    try:
        res = requests.get(url, params=params, timeout=5).json()
        return res.get('works', [])
    except:
        return []

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
            season = options.get('season')
            works = get_anime_data(season_key=season, count=10)
            if not works:
                return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'content': "⚠️ データが見つかりませんでした。"}})

            embeds = []
            for i, work in enumerate(works):
                work_url = work.get('official_site_url') or f"https://annict.com/works/{work.get('id')}"
                embed = {
                    "title": f"{i+1}. {work['title']}",
                    "url": work_url,
                    "color": 0x3498db
                }
                if i == 0:
                    img = (work.get('images', {}).get('recommended_url') or 
                           work.get('images', {}).get('facebook_og_image_url'))
                    if img:
                        embed["image"] = {"url": img}
                    embed["description"] = "🏆 今期の最注目作品"
                embeds.append(embed)

            return jsonify({
                'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE,
                'data': {'content': f"✅ **{datetime.now().year}年 {season.capitalize()} の人気アニメTOP10**", 'embeds': embeds}
            })

        elif cmd_name == 'service':
            work_name = options.get('work_name')
            works = get_anime_data(search_query=work_name, count=3)
            if not works:
                return jsonify({'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE, 'data': {'content': "⚠️ 作品が見つかりませんでした。"}})

            embeds = []
            for work in works:
                q = urllib.parse.quote(work['title'])
                links = (
                    f"[U-NEXT](https://video.unext.jp/search?q={q}) / [Netflix](https://www.netflix.com/search?q={q}) / [Amazon](https://www.amazon.co.jp/s?k={q}+アニメ)\n"
                    f"[dアニメ](https://animestore.docomo.ne.jp/animestore/sch_pc?searchKey={q}) / [Hulu](https://www.hulu.jp/search?q={q}) / [DMM TV](https://tv.dmm.com/vod/search/?search_word={q})"
                )
                work_url = work.get('official_site_url') or f"https://annict.com/works/{work.get('id')}"
                img = (work.get('images', {}).get('recommended_url') or 
                       work.get('images', {}).get('facebook_og_image_url'))
                embeds.append({
                    "title": work['title'],
                    "url": work_url,
                    "description": f"🔍 **配信サイトで検索**:\n{links}",
                    "color": 0xe74c3c,
                    "image": {"url": img or ""}
                })

            return jsonify({
                'type': InteractionResponseType.CHANNEL_MESSAGE_WITH_SOURCE,
                'data': {'content': f"🔍 **「{work_name}」の検索結果**", 'embeds': embeds}
            })

    return jsonify({'type': InteractionResponseType.PONG})

if __name__ == '__main__':
    # ZeaburのPORT設定に対応
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
