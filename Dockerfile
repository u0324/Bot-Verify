FROM mcr.microsoft.com/playwright:v1.41.0-jammy

WORKDIR /app

# システム更新とpipの準備
RUN apt-get update && apt-get install -y python3-pip

# ファイルコピー
COPY . .

# ライブラリインストール
RUN python3 -m pip install --no-cache-dir -r requirements.txt

# ブラウザインストール
RUN playwright install chromium
RUN playwright install-deps

# Flaskサーバー起動 (Gunicorn使用)
CMD ["gunicorn", "main:app", "-b", "0.0.0.0:8080", "--timeout", "120"]
