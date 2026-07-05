# Captur'd hosted service — API + render worker in one image.
# Heavy by nature (headless Chromium + ffmpeg are the product). Split the render
# worker onto its own autoscaling pool later; one box is fine to launch.
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg fonts-dejavu-core ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

# 1) the Captur'd engine (mount or copy the sunsponge-capture repo to ./engine)
COPY engine /srv/engine
RUN pip install --no-cache-dir "/srv/engine[voice]" \
    && python -m playwright install --with-deps chromium

# 2) this service
COPY app /srv/app
COPY web /srv/web
COPY render_worker.py rig /srv/
COPY requirements.txt /srv/
RUN pip install --no-cache-dir -r requirements.txt

ENV CAPTURD_DATA_DIR=/data/capturd \
    CAPTURD_JOBS_DIR=/data/capturd/jobs \
    CAPTURD_REPO=/srv/engine
EXPOSE 8099
CMD ["python","-m","uvicorn","app.main:app","--host","0.0.0.0","--port","8099"]
