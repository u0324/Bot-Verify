FROM python:3.13-slim

# FFmpegとNode.jsをインストール
RUN apt-get update && apt-get install -y \
    ffmpeg \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .

RUN pip install -r requirements.txt

ENV YT_DLP_NO_WARNINGS=1
ENV TMPDIR=/tmp

CMD ["python", "bot.py"]
