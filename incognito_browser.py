"""
无痕浏览模块 - 独立于主回复循环
定时使用无痕浏览器访问指定网址，模拟真人浏览行为
"""

import asyncio
import random
import time
from playwright.async_api import async_playwright


class IncognitoBrowser:
    """无痕浏览器定时访问器

    用法:
        incog = IncognitoBrowser(config)
        incog.start()           # 启动后台任务
        incog.state             # 读取状态（给 webui 用）
        incog.stop()            # 停止
    """

    def __init__(self, config: dict):
        """
        config 字段（从 config.yml 的 incognito_config 读取）:
            - url           要访问的网址（空=禁用）
            - work_start    工作时间开始（小时, 默认8）
            - work_end      工作时间结束（小时, 默认23）
            - interval_min  间隔下限（秒, 默认3600=1h）
            - interval_max  间隔上限（秒, 默认7200=2h）
            - stay_min      页面停留下限（秒, 默认10）
            - stay_max      页面停留上限（秒, 默认60）
        """
        self.url = config.get('url', '')
        self.work_start = config.get('work_start', 8)
        self.work_end = config.get('work_end', 23)
        self.interval_min = config.get('interval_min', 3600)
        self.interval_max = config.get('interval_max', 7200)
        self.stay_min = config.get('stay_min', 10)
        self.stay_max = config.get('stay_max', 60)

        self._task: asyncio.Task | None = None
        self._running = False

        # ---- 对外状态（webui 轮询读取） ----
        self.state = {
            'enabled': bool(self.url),
            'status': '未启动',
            'last_visit': '',
            'visit_count': 0,
            'next_countdown': 0,        # 距下次访问秒数
            'log_lines': [],            # 独立日志环形缓冲区
            # 配置快照（供 webui 显示）
            'config': {
                'url': self.url,
                'work_start': self.work_start,
                'work_end': self.work_end,
                'interval_min': self.interval_min,
                'interval_max': self.interval_max,
                'stay_min': self.stay_min,
                'stay_max': self.stay_max,
            },
        }

    # ---------- 内部 ----------

    MAX_LOG = 200

    def _log(self, msg: str):
        """记录日志到独立缓冲区（仅无痕面板显示，不进入主日志）"""
        ts = time.strftime('%Y-%m-%d %H:%M:%S')
        self.state['log_lines'].append(f"[{ts}] [无痕浏览] {msg}")
        if len(self.state['log_lines']) > self.MAX_LOG:
            self.state['log_lines'] = self.state['log_lines'][-self.MAX_LOG:]

    def _info(self, msg: str):
        self._log(msg)

    def _error(self, msg: str):
        self._log(f"❌ {msg}")

    def _is_work_time(self) -> bool:
        ws, we = self.work_start, self.work_end
        if ws == we:
            return True                     # 允许全天
        h = time.localtime().tm_hour
        if ws < we:
            return ws <= h < we
        else:                               # 跨天（如 22~6）
            return h >= ws or h < we

    def _wait_until_work(self) -> int:
        """返回距下次工作窗口开始的秒数（非工作时间时调用）"""
        h, m, s = time.localtime().tm_hour, time.localtime().tm_min, time.localtime().tm_sec
        ws, we = self.work_start, self.work_end

        if ws < we:
            if h < ws:
                return (ws - h) * 3600 - m * 60 - s
            # h >= we
            return (24 - h + ws) * 3600 - m * 60 - s
        else:  # 跨天
            if h >= ws or h < we:
                return 0
            if h < ws:
                return (ws - h) * 3600 - m * 60 - s
            return (24 - h + ws) * 3600 - m * 60 - s

    # ---------- 核心 ----------

    # 用户代理池（每次随机选一个）
    UA_POOL = [
        # Windows 11 + Chrome
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.208 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.6312.122 Safari/537.36",
        # Windows 11 + Edge
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.2535.92",
        # macOS + Safari
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        # macOS + Firefox
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:127.0) Gecko/20100101 Firefox/127.0",
        # Android + Chrome
        "Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.6422.165 Mobile Safari/537.36",
        "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.179 Mobile Safari/537.36",
        # iPhone + Safari
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Mobile/15E148 Safari/604.1",
    ]

    # 视口大小池
    VIEWPORT_POOL = [
        {"width": 1920, "height": 1080},
        {"width": 1920, "height": 1200},
        {"width": 1536, "height": 864},
        {"width": 1440, "height": 900},
        {"width": 1366, "height": 768},
        {"width": 1280, "height": 720},
        {"width": 1280, "height": 1024},
        {"width": 1680, "height": 1050},
    ]

    # 语言池
    LOCALE_POOL = ["zh-CN", "zh-CN", "zh-CN", "zh-TW", "en-US", "en-US", "ja-JP"]

    @staticmethod
    def _rand_extra_headers() -> dict:
        """每次请求附加的随机 HTTP 头"""
        headers = {
            "Accept-Language": random.choice(["zh-CN,zh;q=0.9", "zh-CN,zh;q=0.9,en;q=0.8", "zh-TW,zh;q=0.9,en;q=0.8", "en-US,en;q=0.9"]),
            "Sec-Ch-Ua": random.choice([
                '"Chromium";v="126", "Google Chrome";v="126", "Not=A?Brand";v="99"',
                '"Chromium";v="125", "Google Chrome";v="125", "Not=A?Brand";v="99"',
                '"Chromium";v="124", "Google Chrome";v="124", "Not=A?Brand";v="99"',
                '"Microsoft Edge";v="126", "Chromium";v="126", "Not=A?Brand";v="99"',
            ]),
            "Sec-Ch-Ua-Mobile": random.choice(["?0", "?0", "?0", "?1"]),
            "Sec-Ch-Ua-Platform": random.choice(['"Windows"', '"Windows"', '"macOS"', '"Android"', '"iOS"']),
        }
        return headers

    async def _visit_once(self, browser):
        """单次无痕访问流程 — 每次使用随机指纹"""
        ua = random.choice(self.UA_POOL)
        vp = random.choice(self.VIEWPORT_POOL)
        locale = random.choice(self.LOCALE_POOL)
        extra_headers = self._rand_extra_headers()

        # 判断是手机端还是桌面端 UA，适当调整视口
        is_mobile = "Mobile" in ua or "iPhone" in ua
        if is_mobile:
            vp = random.choice([
                {"width": 390, "height": 844},
                {"width": 375, "height": 812},
                {"width": 414, "height": 896},
                {"width": 412, "height": 915},
            ])

        self._info(f"🔄 指纹: {ua.split('/')[3].split('.')[0] if 'Chrome' in ua else ua.split('/')[1].split('.')[0]} | {vp['width']}x{vp['height']}")

        context = await browser.new_context(
            locale=locale,
            timezone_id="Asia/Shanghai",
            user_agent=ua,
            viewport=vp,
            extra_http_headers=extra_headers,
        )
        page = await context.new_page()

        try:
            self._info(f"🌐 正在访问 {self.url}")
            await page.goto(self.url, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(random.uniform(1, 3))

            # 点击年龄验证入口
            clicked = False
            try:
                age_link = await page.query_selector('a[onclick*="document.frm.submit"]')
                if age_link:
                    await age_link.click()
                    await asyncio.sleep(random.uniform(2, 4))
                    self._info("✅ 已点击「滿18歲」入口")
                    clicked = True
                else:
                    # 尝试模糊匹配
                    all_links = await page.query_selector_all('a')
                    for link in all_links:
                        onclick = await link.get_attribute('onclick') or ''
                        text = await link.inner_text()
                        if 'submit' in onclick or '18' in text or '滿' in text:
                            await link.click()
                            await asyncio.sleep(random.uniform(2, 4))
                            self._info("✅ 已点击年龄验证链接（模糊匹配）")
                            clicked = True
                            break
                    if not clicked:
                        self._info("⚠️ 未找到年龄验证链接，页面可能已直通")
            except Exception as e:
                self._info(f"⚠️ 点击年龄验证异常: {e}")

            # 随机停留
            stay = random.randint(self.stay_min, self.stay_max)
            self._info(f"⏳ 停留 {stay}s (随机 {self.stay_min}~{self.stay_max}s)...")
            await asyncio.sleep(stay)

            self._info("✅ 无痕访问完成")
            self.state['visit_count'] += 1
            self.state['last_visit'] = time.strftime('%H:%M:%S')

        except Exception as e:
            self._error(f"❌ 访问异常: {e}")
        finally:
            await context.close()

    async def _run_loop(self):
        if not self.url:
            self._info("未配置 URL，无痕浏览已跳过")
            return

        self._running = True
        self.state['status'] = '运行中'
        interval_min_s = self.interval_min
        interval_max_s = self.interval_max
        self._info(
            f"🚀 启动 | 时间窗 {self.work_start}:00~{self.work_end}:00 "
            f"| 间隔 {interval_min_s//60}~{interval_max_s//60}min"
        )

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                ],
            )
            try:
                while self._running:
                    # ---- 工作时间检查 ----
                    if not self._is_work_time():
                        wait = self._wait_until_work()
                        self.state['status'] = f"等待工作时间 ({self.work_start}:00)"
                        self.state['next_countdown'] = wait
                        self._info(f"⏰ 非工作时间，{wait//3600}h{(wait%3600)//60:02d}m 后开始")
                        # 每 30s 醒一次，以便及时检测到进入工作时间
                        slept = 0
                        while slept < wait and self._running:
                            await asyncio.sleep(min(30, wait - slept))
                            slept += 30
                            self.state['next_countdown'] = wait - slept
                        continue

                    # ---- 执行访问 ----
                    self.state['status'] = '正在访问'
                    await self._visit_once(browser)

                    # ---- 随机等待下次访问 ----
                    if not self._running:
                        break
                    interval = random.randint(self.interval_min, self.interval_max)
                    self.state['status'] = f"等待 ({interval//60}min)"
                    self.state['next_countdown'] = interval
                    self._info(f"⏳ 距下次访问 {interval//60}min")

                    # 逐秒递减倒计时（同时持续检测工作时间/停止信号）
                    for tick in range(interval):
                        if not self._running or not self._is_work_time():
                            break
                        self.state['next_countdown'] = interval - tick
                        await asyncio.sleep(1)

            finally:
                await browser.close()

        self.state['status'] = '已停止'
        self.state['next_countdown'] = 0

    # ---------- 外部接口 ----------

    def start(self):
        """启动后台任务（线程安全，多次调用安全）"""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run_loop())

    def stop(self):
        """请求停止"""
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running
