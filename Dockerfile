FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ ./src/
ENV PYTHONPATH=/app/src PORT=8000
EXPOSE 8000
CMD ["python", "-m", "keepit_mcp.server"]
