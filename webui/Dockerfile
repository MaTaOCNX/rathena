# syntax=docker/dockerfile:1
# MaTaO-RO web UI (build context: repository root)

FROM python:3.12-slim AS index
WORKDIR /build
COPY webui/build_items.py ./
COPY db/re/item_db_equip.yml db/re/item_cash_db.txt db/re/
COPY db/import/item_db.yml db/import/item_cash_db.txt db/import/
RUN python build_items.py /build /build/cashshop.json

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY webui/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
 && useradd --system --home-dir /app --shell /usr/sbin/nologin web
COPY webui/app.py ./
COPY webui/templates ./templates
COPY webui/static ./static
COPY --from=index /build/cashshop.json ./data/cashshop.json
USER web
EXPOSE 8080
# --preload: one app instance (and one SECRET_KEY) shared by all workers
CMD ["gunicorn", "--preload", "--workers", "2", "--bind", "0.0.0.0:8080", "--access-logfile", "-", "app:app"]
