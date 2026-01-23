FROM mcr.microsoft.com/playwright:v1.41.0-jammy
WORKDIR /app
RUN apt-get update && apt-get install -y python3-pip
COPY . .
RUN python3 -m pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium
RUN playwright install-deps
CMD ["gunicorn", "bot:app", "-b", "0.0.0.0:8080", "--timeout", "120"]
