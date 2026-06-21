"""
草榴自动回复 - Web 管理界面
Flask + SSE 日志流 + 实时截图 + 配置编辑
"""
import os
import sys
import time
import json
import asyncio
import threading
import logging
import yaml
import io
import re

from flask import Flask, render_template, jsonify, request, Response, send_file

app = Flask(__name__)

CONFIG_PATH = "config.yml"

# ---------- 全局状态（由 AutoReply 主循环更新） ----------
state = {
    "page": None,               # Playwright Page 对象引用
    "status": "启动中",
    "users": [],
    "total_reply_success": 0,
    "total_reply_sent": 0,
    "reply_list_count": 0,
    "last_screenshot": None,    # bytes
    "last_reply_title": "",
    "last_reply_content": "",
    "last_error": "",
    "start_time": time.time(),
    "log_lines": [],            # 内存日志环
    "log_cursor": 0,
    # 定时窗设置（小时制 0~24）— 优先从 config.yml 读取
    "work_time_start": 0,       # 每天几点开始执行（0=全天）
    "work_time_end": 24,        # 每天几点结束执行（24=全天）
    "work_window_text": "",     # 当前时间窗状态文字
    "next_reply_countdown": 0,  # 下次回复倒计时（秒）
    "countdown_text": "",       # 倒计时显示文字
    "user_sleep": 0,            # 当前用户正在休眠的秒数（快照值）
    "user_sleep_ref": 0,        # user_sleep 的基准值（用于时间戳递减）
    "user_sleep_time": 0,       # 记录 user_sleep 时的 time.time()
    "today_reply": 0,           # 今日已回复数
    "daily_limit": 10,           # 每日上限（默认10）
}

MAX_LOG = 500

# ---------- 日志拦截 ----------

class FlaskLogHandler(logging.Handler):
    """拦截 Python logging 输出，同时写入内存环形缓冲区"""
    def emit(self, record):
        try:
            msg = self.format(record) + "\n"
            state["log_lines"].append(msg)
            if len(state["log_lines"]) > MAX_LOG:
                state["log_lines"] = state["log_lines"][-MAX_LOG:]
            state["log_cursor"] += 1
        except Exception:
            pass

def init_log_capture():
    import logging
    # 屏蔽 werkzeug 的 HTTP 请求日志（不把它们送进我们的日志流）
    logging.getLogger('werkzeug').setLevel(logging.ERROR)

    root = logging.getLogger()
    handler = FlaskLogHandler()
    handler.setFormatter(logging.Formatter('[%(asctime)s] [%(levelname)s]\t%(message)s'))
    handler.setLevel(logging.DEBUG)
    root.addHandler(handler)

init_log_capture()

# ---------- 读取配置 ----------

def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf8") as f:
            return yaml.load(f, Loader=yaml.FullLoader) or {}
    except Exception as e:
        return {"error": str(e)}

# 从 config.yml 初始化时间窗状态
_init_cfg = load_config()
_init_gobal = _init_cfg.get("gobal_config", {}) if isinstance(_init_cfg, dict) else {}
_wts = _init_gobal.get("WorkTimeStart", 0)
_wte = _init_gobal.get("WorkTimeEnd", 24)
if isinstance(_wts, int) and isinstance(_wte, int) and 0 <= _wts < _wte <= 24:
    state["work_time_start"] = _wts
    state["work_time_end"] = _wte

# ---------- 截图任务（在事件循环中运行） ----------

async def screenshot_loop():
    """每 2 秒截一次图"""
    while True:
        try:
            p = state.get("page")
            if p and not p.is_closed():
                buf = await p.screenshot(type="jpeg", quality=70, full_page=False)
                state["last_screenshot"] = buf
        except Exception:
            pass
        await asyncio.sleep(2)

# ---------- 倒计时计算（在 Flask 线程实时计算） ----------

def _calc_work_window():
    """实时计算时间窗状态和倒计时，每次 /api/status 调用时执行"""
    import datetime
    ws = state["work_time_start"]
    we = state["work_time_end"]

    now = datetime.datetime.now()
    now_h = now.hour
    now_m = now.minute
    now_s = now.second

    if ws == 0 and we == 24:
        state["work_window_text"] = "全天运行"
        # 用基准值+记录时间计算出实时剩余秒数
        sleep_ref = state["user_sleep_ref"]
        sleep_time = state["user_sleep_time"]
        if sleep_ref > 0 and sleep_time > 0:
            elapsed = int(time.time() - sleep_time)
            remaining = max(0, sleep_ref - elapsed)
            state["next_reply_countdown"] = remaining
            if remaining > 0:
                hh = remaining // 3600
                mm = (remaining % 3600) // 60
                ss = remaining % 60
                if hh > 0:
                    state["countdown_text"] = f"{hh}h{mm:02d}m"
                else:
                    state["countdown_text"] = f"{mm}m{ss:02d}s"
            else:
                state["countdown_text"] = ""
        else:
            state["next_reply_countdown"] = 0
            state["countdown_text"] = ""
        return

    if ws <= now_h < we:
        # 工作时间：显示距离窗口关闭还有多久
        cd = (we - now_h) * 3600 - now_m * 60 - now_s
        cd = max(0, cd)
        state["next_reply_countdown"] = cd
        hh = cd // 3600
        mm = (cd % 3600) // 60
        state["countdown_text"] = f"{hh}h{mm:02d}m"
        state["work_window_text"] = f"工作时间 ({ws}:00~{we}:00)"
    else:
        # 非工作时间：显示距离下次窗口开始还有多久
        if now_h < ws:
            cd = (ws - now_h) * 3600 - now_m * 60 - now_s
        else:
            cd = (24 - now_h + ws) * 3600 - now_m * 60 - now_s
        cd = max(0, cd)
        state["next_reply_countdown"] = cd
        hh = cd // 3600
        mm = (cd % 3600) // 60
        state["countdown_text"] = f"{hh}h{mm:02d}m"
        state["work_window_text"] = f"等待中 ({ws}:00~{we}:00)"

# ---------- Flask 路由 ----------

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/status")
def api_status():
    """返回当前运行状态（JSON）"""
    # 实时计算时间窗状态
    _calc_work_window()

    elapsed = int(time.time() - state["start_time"])
    h, m = divmod(elapsed, 3600)
    m, s = divmod(m, 60)
    cfg = load_config()
    users_cfg = cfg.get("users_config", [])
    gobal = cfg.get("gobal_config", {})

    # 调试：打印 user_sleep 到日志
    logging.debug(f"user_sleep={state.get('user_sleep')} active={state.get('next_reply_countdown')}")

    return jsonify({
        "status": state["status"],
        "runtime": f"{h:02d}:{m:02d}:{s:02d}",
        "users": state["users"],
        "total_reply_success": state["total_reply_success"],
        "total_reply_sent": state["total_reply_sent"],
        "pending_replies": state["reply_list_count"],
        "last_reply_title": state["last_reply_title"],
        "last_reply_content": state["last_reply_content"],
        "last_error": state["last_error"],
        "config_users": [
            {k: v for k, v in u.items() if k != "password"}
            for u in users_cfg
        ],
        "config_gobal": {
            "Host": gobal.get("Host", ""),
            "Fids": gobal.get("Fids", []),
            "PollingMin": gobal.get("PollingMin", 60),
            "PollingMax": gobal.get("PollingMax", 300),
            "ReplyLimit": gobal.get("ReplyLimit", 10),
            "TimeIntervalStart": gobal.get("TimeIntervalStart", 1024),
            "TimeIntervalEnd": gobal.get("TimeIntervalEnd", 2048),
            "ScanPages": gobal.get("ScanPages", 3),
            "ReplyContent": gobal.get("ReplyContent", []),
            "Headless": gobal.get("Headless", True),
            "BarkUrl": gobal.get("BarkUrl", ""),
            "Proxy": gobal.get("Proxy", False),
            "Like": gobal.get("Like", True),
        },
        "work_time_start": state["work_time_start"],
        "work_time_end": state["work_time_end"],
        "work_window_text": state["work_window_text"] or ("全天运行" if state["work_time_start"] == 0 and state["work_time_end"] == 24 else "非工作时间"),
        "next_reply_countdown": state["next_reply_countdown"],
        "countdown_text": state["countdown_text"],
        "today_reply": state["today_reply"],
        "daily_limit": state["daily_limit"],
    })

@app.route("/api/config", methods=["GET"])
def api_get_config():
    """获取完整 config.yml"""
    try:
        with open(CONFIG_PATH, "r", encoding="utf8") as f:
            content = f.read()
        return content, 200, {"Content-Type": "text/plain; charset=utf-8"}
    except Exception as e:
        return str(e), 500

@app.route("/api/config", methods=["POST"])
def api_save_config():
    """保存修改后的 config.yml"""
    try:
        new_content = request.get_data(as_text=True)
        # 简单的 YAML 校验
        yaml.safe_load(new_content)
        with open(CONFIG_PATH, "w", encoding="utf8") as f:
            f.write(new_content)
        return jsonify({"ok": True, "message": "配置已保存，重启后生效"})
    except yaml.YAMLError as e:
        return jsonify({"ok": False, "message": f"YAML 格式错误: {e}"}), 400
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500

@app.route("/api/worktime", methods=["POST"])
def api_set_worktime():
    """设置工作时间窗（同时持久化到 config.yml）"""
    try:
        data = request.get_json()
        start = int(data.get("start", 0))
        end = int(data.get("end", 24))
        if start < 0 or end > 24 or start >= end:
            return jsonify({"ok": False, "message": "时间范围无效"}), 400
        state["work_time_start"] = start
        state["work_time_end"] = end
        logging.info(f"⏰ 工作时间窗已更新: {start}:00 ~ {end}:00")
        # 持久化到 config.yml
        try:
            with open(CONFIG_PATH, "r", encoding="utf8") as f:
                cfg = yaml.safe_load(f) or {}
            if "gobal_config" not in cfg or not isinstance(cfg["gobal_config"], dict):
                cfg["gobal_config"] = {}
            cfg["gobal_config"]["WorkTimeStart"] = start
            cfg["gobal_config"]["WorkTimeEnd"] = end
            with open(CONFIG_PATH, "w", encoding="utf8") as f:
                yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False)
        except Exception as e:
            logging.warning(f"持久化时间窗到 config.yml 失败: {e}")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500

@app.route("/api/screenshot")
def api_screenshot():
    """返回最新截图 (JPEG)"""
    buf = state.get("last_screenshot")
    if buf:
        return send_file(io.BytesIO(buf), mimetype="image/jpeg")
    return "", 204

@app.route("/api/log")
def api_log():
    """SSE 日志流"""
    def generate():
        cursor = state["log_cursor"]
        # 先发送已有日志
        for line in state["log_lines"]:
            yield f"data: {json.dumps({'text': line})}\n\n"
        # 持续监听新日志
        while True:
            if state["log_cursor"] > cursor:
                new_lines = state["log_lines"][-(state["log_cursor"] - cursor):]
                for line in new_lines:
                    yield f"data: {json.dumps({'text': line})}\n\n"
                cursor = state["log_cursor"]
            time.sleep(0.5)
    return Response(generate(), mimetype="text/event-stream")

# ---------- 启动 Web 服务 ----------

def run_flask(port=8888):
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# ==================== 导出给 main.py 的接口 ====================

def start_web_thread(port=8888):
    """在独立线程中启动 Flask"""
    t = threading.Thread(target=run_flask, args=(port,), daemon=True)
    t.start()
    return t

def update_state_from_user(user):
    """由 AutoReply 主循环调用，更新 web 状态"""
    state["users"] = [{
        "name": user.get_username(),
        "invalid": user.get_invalid(),
        "sleep": user.get_sleep_time(),
        "pending": len(user.reply_list),
    }]
    sleep = user.get_sleep_time()
    state["user_sleep"] = sleep
    state["user_sleep_ref"] = sleep
    state["user_sleep_time"] = time.time()
    state["reply_list_count"] = len(user.reply_list)
    if hasattr(user, 'today_reply_count'):
        state["today_reply"] = user.today_reply_count
    if hasattr(user, 'daily_limit'):
        state["daily_limit"] = user.daily_limit

def set_page(page):
    state["page"] = page

def set_status(s):
    state["status"] = s

def record_reply(title, content, success=True):
    state["last_reply_title"] = title
    state["last_reply_content"] = content
    if success:
        state["total_reply_success"] += 1
    state["total_reply_sent"] += 1

def record_error(err):
    state["last_error"] = err
