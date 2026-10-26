# syntax=docker/dockerfile:1
# yoriki-api：api / qcluster / migrate で共用する（docs/deployment.md §2）。
# ベースイメージは mise.toml の版（Python 3.13）に合わせ、digestで固定する
FROM python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH

FROM base AS builder
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
# uvの版は mise.toml に合わせる
RUN pip install --no-cache-dir uv==0.13.1
WORKDIR /build
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-install-project

FROM base
# SQL ServerのConnector（pyodbc）が使うMicrosoft ODBC Driver 18。Microsoftのライセンスのためリポジトリには含めず、
# ビルド時にMicrosoftのリポジトリから取得する。ACCEPT_EULA=Y により、ビルドする人がライセンスに同意する
# （https://aka.ms/odbc18eula。design.md §4.9.2）。このイメージは配布しない（deployment.md §2）
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean \
    && . /etc/os-release \
    && python -c "import urllib.request as r; r.urlretrieve('https://packages.microsoft.com/config/debian/${VERSION_ID}/packages-microsoft-prod.deb', '/tmp/ms.deb')" \
    && dpkg -i /tmp/ms.deb && rm /tmp/ms.deb \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 unixodbc libgssapi-krb5-2
RUN useradd --system --uid 10001 --user-group --home-dir /app --no-create-home yoriki
COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
COPY src/ ./
# 静的ファイルはビルド時に集める。秘密情報は入れない（ダミーの値で設定を読み込むだけ）
RUN YORIKI_SECRET_KEY=collectstatic-only python manage.py collectstatic --noinput -v0
USER yoriki
EXPOSE 8000
CMD ["uvicorn", "app.asgi:application", "--host", "0.0.0.0", "--port", "8000"]
