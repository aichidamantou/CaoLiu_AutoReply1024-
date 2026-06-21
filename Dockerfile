# 草榴自动回复 - 群晖 NAS Docker 版
# 基于 Playwright + Python，无头 Chromium 静默运行
# 构建: docker build -t caoliu-auto-reply .
# 运行: docker compose up -d

FROM python:3.11-slim

LABEL description="草榴自动回帖 - Playwright 浏览器自动化版"

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    wget \
    # 中文字体支持（中文论坛需要）
    fonts-noto-cjk \
    fonts-noto-color-emoji \
    # Playwright 依赖
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

# 安装 Playwright 及 Chromium
RUN pip install --no-cache-dir playwright && \
    playwright install-deps chromium && \
    playwright install chromium

# 安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 时区
ENV TZ=Asia/Shanghai

WORKDIR /app

# 复制程序文件（config.yml 和 cookies 通过 volume 挂载，方便热修改）
COPY AutoReply.py webui.py .
COPY templates/ templates/

# 默认端口：WEB_PORT=8888 时启动 Web 面板，不设置则纯命令行模式
# 也可在 docker-compose 的 environment 中设置

# 群晖上 config.yml 必须设置 Headless: true
# 浏览器无头 + 大视口，适配中文站点
CMD ["python", "AutoReply.py"]
