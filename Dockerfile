FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements-runtime.lock pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir -r requirements-runtime.lock \
    && pip install --no-cache-dir --no-deps . \
    && useradd --uid 10001 --create-home aurum \
    && mkdir /data \
    && chown aurum:aurum /data
USER aurum
VOLUME /data
ENTRYPOINT ["aurum", "--db", "/data/aurum.sqlite"]
CMD ["serve", "--host", "0.0.0.0", "--token-env", "AURUM_API_TOKEN"]
