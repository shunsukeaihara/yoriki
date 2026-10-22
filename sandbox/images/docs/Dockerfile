# syntax=docker/dockerfile:1
# 文書処理用のSandboxイメージ（design.md §7、harness.md §8.5、sandbox/CONTRACT.md）。
# ビルドのcontextは sandbox/（共通の補助コマンド helper/ を使うため）:
#   docker build -f sandbox/images/docs/Dockerfile -t yoriki-sandbox-docs:dev sandbox
# 公開Docker Hubに置くので、秘密情報や社内向けの設定を入れない（design.md §10.1）。

# Pythonの版は mise.toml に合わせる（本体の api/Dockerfile と同じベースイメージ）
FROM python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f

ARG DEBIAN_FRONTEND=noninteractive

# エージェントが使うコマンド（harness.md §8.5）。sudo・apt は Sandbox の中では使わせない（ビルド時だけ入れる）
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bc \
        bzip2 \
        diffutils \
        file \
        findutils \
        fontconfig \
        fonts-noto-cjk \
        gawk \
        jq \
        less \
        procps \
        ripgrep \
        tree \
        unzip \
        xz-utils \
        zip \
    && rm -rf /var/lib/apt/lists/*

# 頻出ライブラリは、全依存の版とhashを固定したlockから、イメージのPythonへ入れる（design.md §7.6）。
# rootfsは読取り専用なので、Sandboxの中からは変えられない。Run専用の環境 /workspace/env は、
# `yoriki-sandbox init-env` がこのPythonの上に薄く作る（追加したpackageだけを持つ）。
# lockは、追加するpackageの依存を解くときに使うので、イメージに残す
COPY images/docs/requirements.lock /opt/yoriki/requirements.lock
RUN pip download --no-cache-dir --disable-pip-version-check --no-deps --only-binary=:all: --require-hashes \
        -r /opt/yoriki/requirements.lock -d /tmp/wheels \
    && pip install --no-cache-dir --disable-pip-version-check --no-index --find-links=/tmp/wheels \
        --only-binary=:all: --require-hashes --no-deps -r /opt/yoriki/requirements.lock \
    && rm -rf /tmp/wheels \
    && python -c "import matplotlib.font_manager as f; f.fontManager" \
    && fc-cache -f

COPY helper/yoriki_sandbox /opt/yoriki/lib/yoriki_sandbox
COPY helper/bin/yoriki-sandbox /opt/yoriki/bin/yoriki-sandbox
RUN python -m compileall -q /opt/yoriki/lib \
    # Sandboxの利用者（design.md §7.2）。HOMEはRunnerが /workspace/work/.home にする
    && groupadd --gid 10001 sandbox \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /workspace/work/.home --shell /bin/bash sandbox \
    # Launcherがmountする場所。中身はRunごとにmountするので、イメージには空のディレクトリだけを置く
    && mkdir -p /workspace/input /workspace/master /workspace/work /workspace/output /workspace/env /packages \
    && chown 10001:10001 /workspace/work /workspace/output /workspace/env

ENV LANG=C.UTF-8 \
    PATH=/workspace/env/bin:/opt/yoriki/bin:/usr/local/bin:/usr/bin:/bin

LABEL org.yoriki.sandbox-contract="1" \
      org.yoriki.sandbox-profile="docs" \
      org.opencontainers.image.title="yoriki-sandbox-docs" \
      org.opencontainers.image.description="Yoriki sandbox for document processing (Python, PDF, Office)" \
      org.opencontainers.image.licenses="MIT"

USER 10001:10001
WORKDIR /workspace/work
# 常駐するのは補助コマンドの init（PID 1）だけ。コマンドは呼出しごとに docker exec で始める（harness.md §8.1）。
# init は親のいなくなったプロセスを回収し、Sandboxの中のコマンドからは止められない。Launcherは --init を付けない
CMD ["/opt/yoriki/bin/yoriki-sandbox", "init"]
