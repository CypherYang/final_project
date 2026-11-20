FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

# gateway 只用 Python 標準函式庫，不需要安裝任何套件
COPY . .

EXPOSE 8082

CMD ["python", "gateway.py"]
