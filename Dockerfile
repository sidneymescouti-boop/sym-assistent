FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home sym && mkdir -p /app/data && chown sym:sym /app/data
COPY server.py ai_gateway.py panel.html panel.js panel.css ./
USER sym
ENV SYM_BIND=0.0.0.0 PORT=8765 SYM_DB=/app/data/sym.sqlite3
EXPOSE 8765
CMD ["python", "server.py"]
