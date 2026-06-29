FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8080

WORKDIR /app

COPY pyproject.toml README.md ./
COPY clashbot ./clashbot

EXPOSE 8080

CMD ["python", "-m", "clashbot.debug_server", "--host", "0.0.0.0"]
