FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --system --uid 10001 --create-home appuser
COPY src ./src
ENV PYTHONPATH=/app/src PORT=8000
USER appuser
EXPOSE 8000
CMD ["python", "-m", "keepit_mcp.server"]
