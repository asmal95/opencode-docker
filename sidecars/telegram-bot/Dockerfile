FROM python:3.12-slim

WORKDIR /app

RUN groupadd -g 1000 botuser && useradd -m -u 1000 -g botuser botuser

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /opt/bot && chown -R botuser:botuser /app /opt/bot

USER botuser

CMD ["python", "bot.py"]