FROM python:3.12-slim

WORKDIR /app
COPY server.py .

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

CMD ["python", "server.py"]
