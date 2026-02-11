FROM python:3.13-slim  
LABEL "language"="python"  
WORKDIR /app  
# Node.js をインストール（yt-dlp の JavaScript ランタイムとして必要）  
RUN apt-get update && apt-get install -y \  
    curl \  
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \  
    && apt-get install -y nodejs \  
    && rm -rf /var/lib/apt/lists/*  
COPY . .  
RUN pip install -r requirements.txt  
EXPOSE 8080  
CMD ["python", "bot.py"]  
