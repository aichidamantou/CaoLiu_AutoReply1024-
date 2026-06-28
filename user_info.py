"""
用户信息查询独立模块
在 Playwright 浏览器中访问 profile.php 获取用户论坛信息
"""

import re
import asyncio
import logging
from urllib.parse import urljoin

log = logging.getLogger('CaoLiu_AutoReply')


class UserInfoFetcher:
    """独立用户信息抓取器，每小时刷新一次"""

    def __init__(self, host: str):
        self.host = host
        self._task: asyncio.Task | None = None
        self._running = False

        # ---- 缓存 & 状态 ----
        self.cache = {
            "name": "",
            "title": "",
            "posts": "?",
            "pres": "?",
            "usd": "?",
            "contribution": "?",
            "last_update": "",
            "raw": "",
        }

    async def fetch(self, page) -> str:
        """抓取用户信息，返回文字摘要，同时更新 self.cache"""
        for attempt in range(3):
            try:
                profile_url = urljoin(self.host, "profile.php")
                await page.goto(profile_url, wait_until="domcontentloaded", timeout=15_000)
                await asyncio.sleep(3)
                body = await page.content()
                if "UID" not in body:
                    return "未登录"

                # 提取用户名
                name = ""
                m = re.search(r"用戶名[：:]\s*([^<]+)", body)
                if m:
                    name = m.group(1).strip()
                if not name:
                    m = re.search(r"用戶名:</td>\s*<td[^>]*>([^<]+)", body)
                    if m:
                        name = m.group(1).strip()
                # 去掉尾部 UID 等多余信息
                name = re.sub(r"[（(]UID.*?[）)]", "", name).strip()
                # 后备：页面标题
                if not name:
                    m = re.search(r"<title>([^<]+个人资料)</title>", body)
                    if m:
                        name = re.sub(r"\s*\(?个人资料\)?\s*", "", m.group(1)).strip()

                title = "?"
                m = re.search(r"會員頭銜[：:]\s*([^<]+)", body)
                if m:
                    title = m.group(1).strip()

                posts = "?"
                m = re.search(r"發帖[：:]?\s*(\d+)", body)
                if m:
                    posts = m.group(1)

                pres = "?"
                m = re.search(r"威望[：:]?\s*(\d+)", body)
                if m:
                    pres = m.group(1)

                usd = "?"
                m = re.search(r"金錢[：:]?\s*(\d+)", body)
                if m:
                    usd = m.group(1)

                contribution = "?"
                m = re.search(r"貢獻[：:]?\s*(\d+)", body)
                if m:
                    contribution = m.group(1)

                self.cache = {
                    "name": name,
                    "title": title,
                    "posts": posts,
                    "pres": pres,
                    "usd": usd,
                    "contribution": contribution,
                    "last_update": __import__('time').strftime('%H:%M:%S'),
                    "raw": f"{name} 发帖{posts} 威望{pres} 金钱{usd} 贡献{contribution}",
                }
                return self.cache["raw"]
            except Exception as e:
                await asyncio.sleep(3)
        self.cache["last_update"] = __import__('time').strftime('%H:%M:%S')
        return "查询失败"

    async def _run_loop(self, get_page_fn, on_update_fn):
        """只启动时抓一次，后续依赖回复成功时触发的 fetch()"""
        self._running = True
        log.info("[用户信息] 已就绪（跟随回复更新）")

        if self._running:
            page = get_page_fn()
            if page and not page.is_closed():
                info = await self.fetch(page)
                log.info(f"[用户信息] {info}")
                if on_update_fn:
                    on_update_fn()

    def start(self, get_page_fn, on_update_fn):
        """启动（只启动时抓一次）"""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(
            self._run_loop(get_page_fn, on_update_fn)
        )

    def stop(self):
        """请求停止"""
        self._running = False
