"""
草榴自动回帖 - Playwright 浏览器自动化版
基于真实 Chrome 浏览器，模拟用户操作，绕过 Cloudflare 防护
"""
import asyncio
import json
import yaml
import random
import re
import os
import sys
import base64
import logging
import time
from typing import List, Optional
from urllib.parse import urljoin
from playwright.async_api import async_playwright, Page, BrowserContext
import pyotp
import requests
DEBUG = False
__version__ = "0.25.06.18.1"
# ==================== 日志 ====================
def outputLog(project_name: str) -> logging.Logger:
    log = logging.getLogger(project_name)
    log.setLevel(logging.DEBUG)
    fmt = logging.Formatter('[%(asctime)s] [%(levelname)s]\t%(message)s')
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG if DEBUG else logging.INFO)
    ch.setFormatter(fmt)
    log.addHandler(ch)
    fh = logging.FileHandler(f'{project_name}.log', encoding='utf-8')
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    log.addHandler(fh)
    return log
# ==================== 配置文件加载 ====================
try:
    with open("config.yml", "r", encoding='utf8') as f:
        config = yaml.load(f, Loader=yaml.FullLoader)
except FileNotFoundError:
    log = outputLog('CaoLiu_AutoReply')
    log.error("配置文件 config.yml 不存在！")
    sys.exit(0)
g = config.get("gobal_config", {})
# 验证码服务配置
captcha_function: Optional[str] = None
captcha_userid: str = ""
captcha_apikey: str = ""
if g.get("truecaptcha_config"):
    captcha_userid = g["truecaptcha_config"]["userid"]
    captcha_apikey = g["truecaptcha_config"]["apikey"]
    captcha_function = "apitruecaptcha"
elif g.get("ttshitu_config"):
    captcha_userid = g["ttshitu_config"]["userid"]
    captcha_apikey = g["ttshitu_config"]["apikey"]
    captcha_function = "ttshitu"
# 全局配置
users_config = config.get("users_config", [])
LogFileName = g.get("LogFileName", "CaoLiu_AutoReply")
AutoUpdate = g.get("AutoUpdate", True)
Fids = g.get("Fids", [g.get("Fid", 7)])  # 回复板块列表，支持多个
PollingMin = g.get("PollingMin", 60)       # 轮询间隔下限（秒）
PollingMax = g.get("PollingMax", 300)      # 轮询间隔上限（秒）
ReplyLimit = g.get("ReplyLimit", 10)
Forbid = g.get("Forbid", True)            # 屏蔽版主
InputSelf = g.get("InputSelf", False)     # 手动输入验证码
LikeEnabled = g.get("Like", True)         # 点赞开关
TimeIntervalStart = g.get("TimeIntervalStart", 1024)
TimeIntervalEnd = g.get("TimeIntervalEnd", 2048)
ReplyContent: List[str] = g.get("ReplyContent", [])
ForbidContent: List[str] = g.get("ForbidContent", [])
Headless = g.get("Headless", False)       # 是否无头模式（建议先用 False 调试）
ChromePath = g.get("ChromePath", "")       # Chrome 可执行文件路径（留空则用 Playwright 内置的）
Proxy = g.get("Proxy", False)
proxies = g.get("Proxies", {}) if Proxy else {}
Host = f"https://{g.get('Host', 't66y.com')}/"
ScanPages = g.get("ScanPages", 3)          # 扫描板块前几页
BarkUrl = g.get("BarkUrl", "")             # Bark 推送 URL，如 https://api.day.app/xxxx/
IncognitoConfig = g.get("Incognito", {})   # 无痕浏览配置
log = outputLog(LogFileName)
# ==================== 验证码识别 API ====================
def apitruecaptcha(content: bytes) -> str:
    """apitruecaptcha 打码"""
    image_b64 = base64.b64encode(content).decode('utf-8')
    url = 'https://api.apitruecaptcha.org/one/gettext'
    data = {'data': image_b64, 'userid': captcha_userid, 'apikey': captcha_apikey}
    try:
        resp = requests.post(url, json.dumps(data), proxies=proxies if Proxy else None)
        result = resp.json()
        code = result.get('result', 'XXXX')
        log.debug(f"apitruecaptcha code: {code}")
        return code
    except Exception as e:
        log.error(f"apitruecaptcha error: {e}")
        return "XXXX"
def ttshitu(content: bytes) -> str:
    """图鉴打码"""
    image_b64 = base64.b64encode(content).decode('utf-8')
    url = 'http://api.ttshitu.com/base64'
    data = {'username': captcha_userid, 'password': captcha_apikey, 'image': image_b64}
    try:
        resp = requests.post(url, json.dumps(data),
                             headers={'Content-Type': 'application/json;charset=UTF-8'},
                             proxies=proxies if Proxy else None)
        rj = resp.json()
        if rj.get("code") == "-1":
            log.error(f"ttshitu api error: {rj.get('message')}")
            return "XXXX"
        return rj['data']['result']
    except Exception as e:
        log.error(f"ttshitu error: {e}")
        return "XXXX"
# ==================== User 类（Playwright 版） ====================
class User:
    """论坛用户，用 Playwright 控制真实浏览器操作"""
    def __init__(self, username: str, password: str, secret: str):
        self.username = username
        self.password = password
        self.secret = secret
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        # 状态
        self.invalid = False
        self.sleep_time = 0
        self.reply_count = 999999 if ReplyLimit == -1 else ReplyLimit  # 兼容旧代码
        self.total_reply_success = 0       # 累计成功回复数
        self.total_reply_sent = 0          # 累计尝试回复数
        self.reply_list: List[str] = []
        self.exclude_content_tids: List[str] = ForbidContent.copy() if ForbidContent else []
        self.cookie_file = f"./{username}_cookies.json"
        # 每日限额
        self.daily_limit = ReplyLimit            # -1=不限制
        self.today_date = ""                     # 当前日期字符串
        self.today_reply_count = 0               # 今日已回复数
        self.profile = {"posts": "?", "pres": "?", "usd": "?", "contribution": "?"}
    async def init_context(self, browser, proxy_settings: Optional[dict] = None):
        """为当前用户创建独立的浏览器上下文（cookie/存储隔离）"""
        self.context = await browser.new_context(
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            proxy=proxy_settings,
        )
        self.context.set_default_timeout(30_000)
        self.page = await self.context.new_page()
        # 拦截 main.js，不让年龄验证遮罩出现
        await self.page.route("**/main.js*", lambda route: route.fulfill(
            body="/* blocked by AutoReply */",
            content_type="application/javascript",
        ))
        # 设置 ismob=0 cookie 跳过年龄验证（服务端双保险）
        domain = Host.split("//")[1].rstrip("/")
        await self.context.add_cookies([{
            "name": "ismob",
            "value": "0",
            "domain": domain,
            "path": "/",
        }])
        # 加载已保存的 cookies
        await self._load_cookies()
    async def close(self):
        if self.context:
            await self.context.close()
            self.context = None
    # ---------- cookies ----------
    async def _save_cookies(self):
        """保存 cookies 到文件"""
        try:
            cookies = await self.context.cookies()
            with open(self.cookie_file, 'w', encoding='utf-8') as f:
                json.dump(cookies, f, ensure_ascii=False, indent=2)
            log.debug(f"[{self.username}] cookies 已保存")
        except Exception as e:
            log.debug(f"[{self.username}] 保存 cookies 失败: {e}")
    async def _load_cookies(self):
        """从文件加载 cookies"""
        if not os.path.exists(self.cookie_file):
            return
        try:
            with open(self.cookie_file, 'r', encoding='utf-8') as f:
                cookies = json.load(f)
            if cookies:
                await self.context.add_cookies(cookies)
                log.debug(f"[{self.username}] cookies 已加载 ({len(cookies)} 条)")
        except Exception as e:
            log.debug(f"[{self.username}] 加载 cookies 失败: {e}")
    # ---------- 登录 ----------
    async def check_cookies_and_login(self):
        """校验 cookies，无效则重新登录"""
        if await self._is_logged_in():
            if await self._is_banned():
                log.info(f"[{self.username}] 账号已被禁言")
                self.invalid = True
                return
            log.info(f"[{self.username}] cookies 有效，登录成功")
            return
        log.info(f"[{self.username}] cookies 已过期，尝试重新登录")
        if await self._login():
            await self._save_cookies()
            if await self._is_banned():
                log.info(f"[{self.username}] 账号已被禁言")
                self.invalid = True
                return
            log.info(f"[{self.username}] 登录成功")
        else:
            log.info(f"[{self.username}] 登录失败")
            self.invalid = True
    async def _is_logged_in(self) -> bool:
        """检查是否已登录 — 访问 profile.php 看是否有用户信息"""
        try:
            profile_url = urljoin(Host, "profile.php")
            await self.page.goto(profile_url, wait_until="domcontentloaded", timeout=20_000)
            await self._rand_sleep(2, 3)
            body = await self.page.content()
            return self.username in body and "UID" in body
        except Exception as e:
            log.debug(f"[{self.username}] 登录状态检查失败: {e}")
            return False
    async def _is_banned(self) -> bool:
        body = await self.page.content()
        return "禁止發言" in body
    async def _login(self) -> bool:
        """浏览器自动登录（支持两步验证和验证码）"""
        for attempt in range(3):
            try:
                await self.page.goto(urljoin(Host, "login.php"), wait_until="domcontentloaded")
                await self._rand_sleep(2, 4)
                body = await self.page.content()
                if "您已經為會員身份" in body or self.username in body and "UID" in body:
                    log.info(f"[{self.username}] 已为登录状态，跳过登录流程")
                    return True
                if "驗證碼" in body:
                    log.info(f"[{self.username}] 需要验证码")
                    await self._solve_captcha()
                    await self._rand_sleep(3, 5)
                    body = await self.page.content()
                    if "您已經為會員身份" in body:
                        return True
                await self.page.fill('input[name="pwuser"]', self.username)
                await self.page.fill('input[name="pwpwd"]', self.password)
                await self.page.check('input[name="cktime"]')
                await self.page.click('input[type="submit"]')
                await self._rand_sleep(3, 5)
                body = await self.page.content()
                if "兩步驗證" in body or "oneCode" in body:
                    log.info(f"[{self.username}] 需要两步验证")
                    token = pyotp.TOTP(self.secret).now()
                    await self.page.fill('input[name="oneCode"]', token)
                    await self.page.click('input[type="submit"]')
                    await self._rand_sleep(3, 5)
                    body = await self.page.content()
                if "驗證碼" in body:
                    log.info(f"[{self.username}] 需要验证码")
                    await self._solve_captcha()
                    await self._rand_sleep(3, 5)
                    body = await self.page.content()
                if "您已經順利登錄" in body or "您已經為會員身份" in body or "上次登錄時間" in body:
                    return True
                log.warning(f"[{self.username}] 第 {attempt+1} 次登录尝试未检测到成功标志")
            except Exception as e:
                log.error(f"[{self.username}] 登录异常 (第 {attempt+1} 次): {e}")
            if attempt < 2:
                wait = random.randint(15, 45)
                log.info(f"[{self.username}] 等待 {wait}s 后重试登录...")
                await asyncio.sleep(wait)
        return False
    async def _solve_captcha(self):
        """识别并填入验证码"""
        captcha_img = await self.page.query_selector('#imgVerCode, img[src*="codeimg"]')
        if not captcha_img:
            log.warning(f"[{self.username}] 未找到验证码图片")
            return
        img_src = await captcha_img.get_attribute('src')
        if not img_src:
            return
        img_url = urljoin(Host, img_src)
        resp = await self.context.request.get(img_url)
        image_bytes = await resp.body()
        if InputSelf:
            with open("./captcha.png", "wb") as f:
                f.write(image_bytes)
            code = input(f"[{self.username}] 请输入验证码（图片已保存为 captcha.png）: ")
            os.remove("./captcha.png")
        else:
            if captcha_function == "apitruecaptcha":
                code = apitruecaptcha(image_bytes)
            elif captcha_function == "ttshitu":
                code = ttshitu(image_bytes)
            else:
                log.error("未配置验证码识别服务")
                code = "XXXX"
        if code and code != "XXXX":
            await self.page.fill('input[name="validate"]', code)
            await self.page.click('input[type="submit"]')
            log.info(f"[{self.username}] 验证码已提交: {code}")
        else:
            log.warning(f"[{self.username}] 验证码识别结果为空，跳过")
    # ---------- 获取帖子列表 ----------
    async def get_personal_posted_list(self):
        """获取自己已回复过的帖子列表（多页翻取）"""
        if os.path.exists(self.cookie_file.replace('_cookies.json', '_replied.json')):
            try:
                with open(self.cookie_file.replace('_cookies.json', '_replied.json'), 'r', encoding='utf-8') as f:
                    saved = json.load(f)
                if isinstance(saved, list) and len(saved) > 0:
                    self.exclude_content_tids = saved
                    log.info(f"[{self.username}] 从文件加载已回复帖子: {len(self.exclude_content_tids)} 条")
                    return
            except Exception as e:
                log.debug(f"[{self.username}] 加载已回复缓存失败: {e}")

        try:
            for page in range(1, 21):  # 最多翻 20 页
                url = urljoin(Host, f"personal.php?action=post&page={page}")
                await self.page.goto(url, wait_until="domcontentloaded")
                await self._rand_sleep(2, 3)
                body = await self.page.content()

                found = 0
                for m in re.finditer(r'<a\s+[^>]*href="/*([^"]+)"[^>]+class="a2">', body):
                    tid = self._extract_tid(m.group(1))
                    if tid:
                        self.exclude_content_tids.append(tid)
                        found += 1
                if found == 0:
                    break  # 没有更多了
                log.debug(f"[{self.username}] 第{page}页: {found} 帖")
            log.info(f"[{self.username}] 已回复帖子总数: {len(self.exclude_content_tids)}")
        except Exception as e:
            log.error(f"[{self.username}] 获取已回复列表失败: {e}")
        # 保存到文件，Docker 重启后可用
        self._save_replied_cache()
    async def get_today_list(self, fids: List[int], pages: int = 3):
        """获取今日新帖列表（多板块、多页）"""
        for fid in fids:
            moderators: List[str] = []
            for page in range(1, pages + 1):
                try:
                    page_url = urljoin(Host, f"thread0806.php?fid={fid}&page={page}")
                    await self.page.goto(page_url, wait_until="domcontentloaded")
                    await self._rand_sleep(2, 4)
                    body = await self.page.content()
                    if page == 1:
                        m_mod = re.search(r"版主:([\s\S]*?)</span>", body)
                        if m_mod:
                            moderators = re.findall(r"username=(\w+)", m_mod.group(1))
                        log.debug(f"版主列表: {moderators}")
                    tbody_m = re.search(
                        r'<tbody[^>]*id="tbody"[^>]*>(.*?)</tbody>', body, re.DOTALL
                    )
                    if not tbody_m:
                        log.warning(f"[{self.username}] Fid={fid} page={page} 无 tbody")
                        continue
                    tbody_html = tbody_m.group(1)
                    for tr_block in re.finditer(
                        r'<tr class="tr3 t_one tac">(.*?)</tr>', tbody_html, re.DOTALL
                    ):
                        tr = tr_block.group(1)
                        if "Top-marks" in tr:
                            continue
                        tds = re.findall(r'<td.*?>(.*?)</td>', tr, re.DOTALL)
                        if len(tds) < 3:
                            continue
                        url_m = re.search(
                            r'<h3>.*?<a\s+[^>]*href="/*([^"]+)"[^>]*>(.*?)</a>',
                            tds[1], re.DOTALL,
                        )
                        if not url_m:
                            continue
                        raw_url = url_m.group(1)
                        title = re.sub(r'<.*?>', '', url_m.group(2)).strip()
                        if "thread_page" not in tds[1]:
                            log.debug(f"[Fid={fid}] 跳过单页: {title[:30]}")
                            continue
                        if "read.php" in raw_url:
                            full_url = await self._resolve_pseudo_static(raw_url)
                            if not full_url:
                                continue
                        else:
                            full_url = urljoin(Host, raw_url)
                        author_m = re.search(
                            r'<a href=".*?" class="bl">(.*?)</a>', tds[2], re.DOTALL
                        )
                        author = author_m.group(1).strip() if author_m else ""
                        tid = self._extract_tid(full_url)
                        if Forbid and author in moderators:
                            log.debug(f"跳过版主: {title[:30]}")
                            continue
                        if tid and tid in self.exclude_content_tids:
                            log.debug(f"跳过已回: {title[:30]}")
                            continue
                        self.reply_list.append(full_url)
                except Exception as e:
                    log.error(f"[{self.username}] Fid={fid} page={page} 失败: {e}")
                    continue
            log.info(f"[{self.username}] Fid={fid} 完成")
        log.info(f"[{self.username}] 总计待回复: {len(self.reply_list)} 帖")
    async def _resolve_pseudo_static(self, url: str) -> Optional[str]:
        """从伪静态 URL 解析真实地址"""
        try:
            resp = await self.context.request.get(urljoin(Host, url))
            text = await resp.text()
            m = re.search(
                r'<a\s+[^>]*href="/*([^"]+)"[^>]* class="s5">如果您的瀏覽器沒有自動跳轉',
                text,
            )
            if m:
                return urljoin(Host, m.group(1))
            if "read.php" in resp.url:
                return resp.url
        except Exception as e:
            log.debug(f"解析伪静态失败: {e}")
        return None
    # ---------- 回复 ----------
    def get_one_link(self) -> Optional[str]:
        if not self.reply_list:
            return None
        idx = random.randint(0, len(self.reply_list) - 1)
        url = self.reply_list.pop(idx)
        return url
    async def browse(self, url: str):
        try:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            await self._rand_sleep(2, 5)
        except Exception as e:
            log.warning(f"[{self.username}] 浏览 {url} 异常: {e}")
    async def reply(self, url: str) -> str:
        # 每日限额检查
        if self.daily_limit != -1:
            today = time.strftime("%Y-%m-%d")
            if today != self.today_date:
                self.today_date = today
                self.today_reply_count = 0
            if self.today_reply_count >= self.daily_limit:
                log.info(f"[{self.username}] 今日已回复 {self.today_reply_count} 次，达到上限")
                return "daily_limit"
        # 兼容旧 reply_count
        if self.reply_count <= 0 and self.daily_limit == -1:
            self.reply_count = 999999
        self.total_reply_sent += 1
        try:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            await self._rand_sleep(2, 4)
            title = await self._get_reply_title()
            content_text = random.choice(ReplyContent) if ReplyContent else "感谢分享"
            ta = await self.page.query_selector('textarea[name="atc_content"]')
            if not ta:
                await self.page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                await asyncio.sleep(1)
                ta = await self.page.query_selector('textarea[name="atc_content"]')
            if not ta:
                log.warning(f"[{self.username}] 未找到回复框: {url}")
                return "no_textarea"
            await ta.fill(content_text)
            ti = await self.page.query_selector('input[name="atc_title"]')
            if ti:
                disabled = await ti.get_attribute("disabled")
                readonly = await ti.get_attribute("readonly")
                if not disabled and not readonly:
                    await ti.fill(title if title else "Re: ")
            btn = await self.page.query_selector(
                'input[name="Submit"], input[value*="提交回覆"], '
                'input[type="submit"][value*="回覆"]'
            )
            if btn:
                await btn.click()
            else:
                await ta.press('Control+Enter')
            await self._rand_sleep(3, 5)
            body = await self.page.content()

            # 明确失败检测（这些才真失败）
            if "每日最多能發" in body:
                log.info(f"[{self.username}] 每日上限")
                return "daily_limit"
            if "請先登錄論壇" in body:
                log.info(f"[{self.username}] 未登录")
                return "not_logged_in"
            if "管理員禁言" in body:
                log.info(f"[{self.username}] 禁言")
                return "banned"
            if "灌水預防機制" in body:
                log.info(f"[{self.username}] 灌水预防机制触发")
                return "spam"
            if "該貼已被鎖定" in body:
                log.info(f"[{self.username}] 帖子锁定")
                return "locked"
            if "標題為空" in body or "文章長度錯誤" in body:
                log.warning(f"[{self.username}] 标题/内容异常")
                return "title_error"

            # 只要没匹配到明确失败，都算成功（论坛改版后不再返回"發貼完畢"页面）
            self.reply_count -= 1
            if self.daily_limit != -1:
                self.today_reply_count += 1
            self.total_reply_success += 1
            status_str = f"今日 {self.today_reply_count}/{self.daily_limit}" if self.daily_limit != -1 else f"会话 {self.reply_count}"
            log.info(f"[{self.username}] 回复成功 → 「{title}」 {content_text} {status_str}")
            from bark_push import push_reply_success
            push_reply_success(BarkUrl, self.username,
                               self.total_reply_success,
                               posts=self.profile.get("posts", "?"),
                               pres=self.profile.get("pres", "?"),
                               usd=self.profile.get("usd", "?"),
                               contribution=self.profile.get("contribution", "?"))
            # 追加到已回复列表并保存
            reply_tid = self._extract_tid(url)
            if reply_tid and reply_tid not in self.exclude_content_tids:
                self.exclude_content_tids.append(reply_tid)
                self._save_replied_cache()
            return "success"
        except Exception as e:
            log.error(f"[{self.username}] 回复异常 {url}: {e}")
            return "exception"
    async def _get_reply_title(self) -> str:
        try:
            el = await self.page.query_selector('td:has-text("本頁主題")')
            if el:
                txt = await el.inner_text()
                txt = re.sub(r'^.*本頁主題[：:]?\s*', '', txt).strip()
                if txt:
                    return "Re:" + txt
        except Exception:
            pass
        return "Re: "
    # ---------- 点赞 ----------
    async def like(self, url: str):
        if not LikeEnabled:
            return
        try:
            body = await self.page.content()
            ids = re.findall(r'<a\s+name="?#?(\d+)"?>\s*</a>', body)
            if not ids:
                return
            reply_id = random.choice(ids)
            post_url = urljoin(Host, "api.php")
            result = await self.page.evaluate(
                """async (args) => {
                    const [url, params] = args;
                    const fd = new URLSearchParams();
                    fd.append('action', 'clickLike');
                    fd.append('id', params.id);
                    fd.append('page', 'h');
                    fd.append('json', '1');
                    fd.append('url', params.url);
                    const res = await fetch(url, {
                        method: 'POST',
                        headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                        body: fd,
                    });
                    return await res.text();
                }""",
                [post_url, {"id": reply_id, "url": url}],
            )
            rj = json.loads(result)
            if rj.get("myMoney") and int(rj["myMoney"]) > 0:
                log.info(f"[{self.username}] 点赞成功 (#{reply_id})")
        except Exception as e:
            log.debug(f"[{self.username}] 点赞异常: {e}")
    # ---------- 工具 ----------
    @staticmethod
    def _extract_tid(url: str) -> Optional[str]:
        m = re.search(r"(?:/(\d+)\.html|tid=(\d+))", url)
        if m:
            return m.group(1) or m.group(2)
        return None
    def _save_replied_cache(self):
        """将已回复帖子 tid 列表保存到文件，Docker 重启后可恢复"""
        try:
            path = self.cookie_file.replace('_cookies.json', '_replied.json')
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(list(set(self.exclude_content_tids)), f, ensure_ascii=False)
        except Exception as e:
            log.debug(f"[{self.username}] 保存已回复缓存失败: {e}")
    @staticmethod
    async def _rand_sleep(lo: int, hi: int):
        await asyncio.sleep(random.randint(lo, hi))
    def get_username(self) -> str:
        return self.username
    def set_invalid(self):
        self.invalid = True
    def get_invalid(self) -> bool:
        return self.invalid
    def set_sleep_time(self, t: int):
        self.sleep_time = t
    def get_sleep_time(self) -> int:
        return self.sleep_time
# ==================== 主流程 ====================
async def main():
    log.info(f"CaoLiu_AutoReply v{__version__} (Playwright) 启动")
    if not users_config:
        log.error("config.yml 中未配置任何用户")
        sys.exit(1)
    if not ReplyContent:
        log.error("config.yml 中未配置 ReplyContent")
        sys.exit(1)
    pw_proxy = None
    if Proxy and proxies:
        server = proxies.get("http") or proxies.get("https")
        if server:
            pw_proxy = {"server": server}
    async with async_playwright() as p:
        launch_kwargs = dict(
            headless=Headless,
            proxy=pw_proxy,
        )
        if ChromePath:
            launch_kwargs["executable_path"] = ChromePath
        browser = await p.chromium.launch(**launch_kwargs,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        valid_users: List[User] = []
        for uc in users_config:
            u = User(
                username=uc.get("user", ""),
                password=uc.get("password", ""),
                secret=uc.get("secret", ""),
            )
            if not u.username or not u.password:
                log.warning("跳过配置不完整的用户")
                continue
            await u.init_context(browser, pw_proxy)
            await u.check_cookies_and_login()
            if u.get_invalid():
                await u.close()
                continue
            await u.get_personal_posted_list()
            await u.get_today_list(Fids, ScanPages)
            if not DEBUG:
                st = random.randint(TimeIntervalStart, TimeIntervalEnd)
                log.info(f"[{u.get_username()}] 初始等待 {st}s")
                u.set_sleep_time(st)
                webui.state["user_sleep"] = st
                webui.state["user_sleep_ref"] = st
                webui.state["user_sleep_time"] = time.time()
            valid_users.append(u)
        if not valid_users:
            log.error("没有有效用户，退出")
            await browser.close()
            return
        log.info("=" * 50)
        for u in valid_users:
            log.info(f"[{u.get_username()}] 已登录 — 待回复 {len(u.reply_list)} 帖")
        log.info("=" * 50)
        try:
            stats_counter = 0
            while True:
                all_done = True
                for u in valid_users:
                    if u.get_invalid():
                        continue
                    all_done = False
                    if u.get_sleep_time() > 0:
                        continue
                    url = u.get_one_link()
                    if url is None:
                        log.info(f"[{u.get_username()}] 没有更多帖子")
                        u.set_invalid()
                        continue
                    await u.browse(url)
                    reply_result = await u.reply(url)
                    if reply_result != "success":
                        if reply_result == "daily_limit":
                            log.info(f"[{u.get_username()}] 达每日上限，等待次日重置")
                            # 睡到第二天凌晨（整点后16分钟，避开0点大量定时任务）
                            now = time.localtime()
                            seconds_to_midnight = (23 - now.tm_hour) * 3600 + (59 - now.tm_min) * 60 + (60 - now.tm_sec) + 960
                            u.set_sleep_time(seconds_to_midnight)
                            continue
                        else:
                            log.info(f"[{u.get_username()}] 回复失败 ({reply_result})")
                            u.set_invalid()
                            continue
                    await u.like(url)
                    # 每次回复成功后刷新用户信息
                    if u.page and not u.page.is_closed():
                        await uif.fetch(u.page)
                        webui.update_state_from_user(u)
                    stats_counter += 1
                    if stats_counter % 5 == 0:
                        for u2 in valid_users:
                            if not u2.get_invalid():
                                bar = f"[{u2.get_username()}] {u2.total_reply_success}/{u2.total_reply_sent} 成功"
                                log.info(bar)
                    st = random.randint(TimeIntervalStart, TimeIntervalEnd)
                    log.debug(f"[{u.get_username()}] 休息 {st}s")
                    u.set_sleep_time(st)
                if all_done:
                    log.info("=" * 50)
                    for u2 in valid_users:
                        log.info(f"[{u2.get_username()}] {u2.total_reply_success}/{u2.total_reply_sent} 成功")
                        from bark_push import push_finish; push_finish(BarkUrl, u2.get_username(), u2.total_reply_success)
                    log.info("=" * 50)
                    log.info("退出")
                    break
                sleep_loop = random.randint(PollingMin, PollingMax)
                await asyncio.sleep(sleep_loop)
                for u in valid_users:
                    if u.get_sleep_time() > 0:
                        u.set_sleep_time(max(0, u.get_sleep_time() - sleep_loop))
                    # 每轮都更新 webui 状态（待回复数、今日统计、休眠等）
                    webui.update_state_from_user(u)
        except KeyboardInterrupt:
            log.info("收到 Ctrl+C...")
        finally:
            for u in valid_users:
                if u.context and not u.get_invalid():
                    await u._save_cookies()
                await u.close()
            await browser.close()
            log.info("程序结束")
# ==================== Web UI 集成 ====================
# 当 WEB_PORT 环境变量设置时，启动 Flask Web 面板
WEB_PORT = os.environ.get("WEB_PORT")
def run_with_web():
    """在 Web 模式下运行（启动 Flask + 截图）"""
    import webui
    webui.start_web_thread(int(WEB_PORT))
    webui.set_status("启动中...")
    asyncio.run(main_with_web(webui))
async def main_with_web(webui):
    """带 Web UI 的 main"""
    from playwright.async_api import async_playwright
    from incognito_browser import IncognitoBrowser
    from user_info import UserInfoFetcher
    log.info(f"CaoLiu_AutoReply v{__version__} (Playwright + WebUI) 启动")
    log.info(f"Web 面板: http://0.0.0.0:{WEB_PORT}/")
    if not users_config:
        log.error("config.yml 中未配置任何用户")
        return
    if not ReplyContent:
        log.error("config.yml 中未配置 ReplyContent")
        return
    pw_proxy = None
    if Proxy and proxies:
        server = proxies.get("http") or proxies.get("https")
        if server:
            pw_proxy = {"server": server}
    async with async_playwright() as p:
        launch_kwargs = dict(
            headless=True,  # WebUI 模式下强制无头，截图代替
            proxy=pw_proxy,
        )
        if ChromePath:
            launch_kwargs["executable_path"] = ChromePath
        browser = await p.chromium.launch(**launch_kwargs,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        # ---------- 工作时间窗检查 ----------
        def is_work_time():
            h = time.localtime().tm_hour
            ws = webui.state["work_time_start"]
            we = webui.state["work_time_end"]
            return ws <= h < we
        last_work_check = 0
        def update_countdown_info():
            nonlocal last_work_check
            now_h = time.localtime().tm_hour
            now_m = time.localtime().tm_min
            ws = webui.state["work_time_start"]
            we = webui.state["work_time_end"]
            if ws == 0 and we == 24:
                webui.state["work_window_text"] = "全天运行"
                webui.state["next_reply_countdown"] = 0
                webui.state["countdown_text"] = ""
                return
            if is_work_time():
                webui.state["work_window_text"] = f"工作时间 ({ws}:00~{we}:00)"
            else:
                # 计算距下次开启还有多久
                next_start = (ws - now_h) % 24
                if next_start == 0 and now_m > 0:
                    next_start = 24
                cd = next_start * 3600 - now_m * 60 - time.localtime().tm_sec
                webui.state["next_reply_countdown"] = max(0, cd)
                hh, mm = divmod(max(0, cd) // 60, 60)
                webui.state["countdown_text"] = f"{int(hh)}h{int(mm):02d}m"
                webui.state["work_window_text"] = f"等待中 ({ws}:00~{we}:00)"
        valid_users: List[User] = []
        setup_success = True
        for uc in users_config:
            u = User(
                username=uc.get("user", ""),
                password=uc.get("password", ""),
                secret=uc.get("secret", ""),
            )
            if not u.username or not u.password:
                log.warning("跳过配置不完整的用户")
                continue
            await u.init_context(browser, pw_proxy)
            await u.check_cookies_and_login()
            if u.get_invalid():
                await u.close()
                setup_success = False
                continue
            await u.get_personal_posted_list()
            await u.get_today_list(Fids, ScanPages)
            if not DEBUG:
                st = random.randint(TimeIntervalStart, TimeIntervalEnd)
                log.info(f"[{u.get_username()}] 初始等待 {st}s")
                u.set_sleep_time(st)
                webui.state["user_sleep"] = st
                webui.state["user_sleep_ref"] = st
                webui.state["user_sleep_time"] = time.time()
            webui.update_state_from_user(u)
            valid_users.append(u)
        # 初始化无痕浏览
        incognito = IncognitoBrowser(IncognitoConfig)
        webui.set_incognito(incognito)   # 把引用传给 webui 读取状态
        incognito.start()
        log.info(f"[无痕浏览] {'已启用' if incognito.state['enabled'] else '未配置'}")

        # 初始化用户信息定时刷新
        uif = UserInfoFetcher(Host)
        webui.set_user_info_fetcher(uif)
        def _get_page():
            for u in valid_users:
                if u.page and not u.page.is_closed():
                    return u.page
            return None
        def _on_info_update():
            for u in valid_users:
                webui.update_state_from_user(u)
                # 同步 profile 到 User 对象（给 bark 推送用）
                u.profile = {
                    "posts": uif.cache.get("posts", "?"),
                    "pres": uif.cache.get("pres", "?"),
                    "usd": uif.cache.get("usd", "?"),
                    "contribution": uif.cache.get("contribution", "?"),
                }
        uif.start(_get_page, _on_info_update)

        webui.set_status(f"已登录 {len(valid_users)} 个用户")
        # 初始化 webui 状态
        for u in valid_users:
            webui.state["today_reply"] = u.today_reply_count
            webui.state["daily_limit"] = u.daily_limit
        if not valid_users:
            log.error("没有有效用户，退出")
            await browser.close()
            return
        log.info("=" * 50)
        for u in valid_users:
            log.info(f"[{u.get_username()}] 已登录 — 待回复 {len(u.reply_list)} 帖")
            webui.update_state_from_user(u)
        log.info("=" * 50)
        # ---------- 工作时间窗 ----------
        def _is_work_time():
            h = time.localtime().tm_hour
            ws = webui.state["work_time_start"]
            we = webui.state["work_time_end"]
            return ws <= h < we
        _last_wt_check = 0
        def _update_countdown():
            nonlocal _last_wt_check
            now_h = time.localtime().tm_hour
            now_m = time.localtime().tm_min
            now_s = time.localtime().tm_sec
            ws = webui.state["work_time_start"]
            we = webui.state["work_time_end"]
            if ws == 0 and we == 24:
                webui.state["work_window_text"] = "全天运行"
                slp_ref = webui.state["user_sleep_ref"]
                slp_time = webui.state["user_sleep_time"]
                if slp_ref > 0 and slp_time > 0:
                    elapsed = int(time.time() - slp_time)
                    remaining = max(0, slp_ref - elapsed)
                    webui.state["next_reply_countdown"] = remaining
                    if remaining > 0:
                        hh, mm = divmod(remaining // 60, 60)
                        ss = remaining % 60
                        if hh > 0:
                            webui.state["countdown_text"] = f"{hh}h{mm:02d}m"
                        else:
                            webui.state["countdown_text"] = f"{mm}m{ss:02d}s"
                    else:
                        webui.state["countdown_text"] = ""
                else:
                    webui.state["next_reply_countdown"] = 0
                    webui.state["countdown_text"] = ""
                return
            if _is_work_time():
                cd = (we - now_h) * 3600 - now_m * 60 - now_s
                cd = max(0, cd)
                webui.state["next_reply_countdown"] = cd
                hh, mm = divmod(cd // 60, 60)
                webui.state["countdown_text"] = f"{hh}h{mm:02d}m"
                webui.state["work_window_text"] = f"工作时间 ({ws}:00~{we}:00)"
            else:
                if now_h < ws:
                    cd = (ws - now_h) * 3600 - now_m * 60 - now_s
                else:
                    cd = (24 - now_h + ws) * 3600 - now_m * 60 - now_s
                cd = max(0, cd)
                webui.state["next_reply_countdown"] = cd
                hh, mm = divmod(cd // 60, 60)
                webui.state["countdown_text"] = f"{hh}h{mm:02d}m"
                webui.state["work_window_text"] = f"等待中 ({ws}:00~{we}:00)"
        try:
            stats_counter = 0
            while True:
                # 工作时间窗
                _update_countdown()
                if not _is_work_time():
                    webui.set_status(f"⏰ 非工作时间 — {webui.state['countdown_text']} 后开始")
                    if time.time() - _last_wt_check > 300:
                        log.info(f"⏰ 非工作时间 ({time.localtime().tm_hour}:00)，下次工作: {webui.state['countdown_text']} 后")
                        _last_wt_check = time.time()
                    await asyncio.sleep(60)
                    for u in valid_users:
                        if u.get_sleep_time() > 0:
                            u.set_sleep_time(max(0, u.get_sleep_time() - 60))
                    continue
                else:
                    # 进入工作时间或时间窗被改为全天，重置状态
                    webui.set_status(f"🟢 工作时间 — {webui.state.get('work_window_text', '')}")
                    _last_wt_check = 0
                all_done = True
                for u in valid_users:
                    if u.get_invalid():
                        continue
                    all_done = False
                    if u.get_sleep_time() > 0:
                        continue
                    url = u.get_one_link()
                    if url is None:
                        log.info(f"[{u.get_username()}] 没有更多帖子")
                        u.set_invalid()
                        continue
                    webui.set_status(f"正在回复: {url.split('/')[-1][:40]}")
                    await u.browse(url)
                    reply_result = await u.reply(url)
                    # 记录到 webui
                    webui.record_reply(
                        webui.state.get("last_reply_title", ""),
                        random.choice(ReplyContent) if ReplyContent else "感谢分享",
                        reply_result == "success",
                    )
                    if reply_result != "success":
                        if reply_result == "daily_limit":
                            log.info(f"[{u.get_username()}] 达每日上限，等待次日重置")
                            # 睡到第二天凌晨（整点后16分钟，避开0点大量定时任务）
                            now = time.localtime()
                            seconds_to_midnight = (23 - now.tm_hour) * 3600 + (59 - now.tm_min) * 60 + (60 - now.tm_sec) + 960
                            u.set_sleep_time(seconds_to_midnight)
                            webui.update_state_from_user(u)
                            webui.set_status(f"每日上限，等待 {seconds_to_midnight//3600}h 后重置")
                            continue
                        else:
                            log.info(f"[{u.get_username()}] 回复失败 ({reply_result})")
                            u.set_invalid()
                            webui.set_status(f"回复失败: {reply_result}")
                            continue
                    await u.like(url)
                    stats_counter += 1
                    if stats_counter % 5 == 0:
                        for u2 in valid_users:
                            if not u2.get_invalid():
                                bar = f"[{u2.get_username()}] {u2.total_reply_success}/{u2.total_reply_sent} 成功"
                                log.info(bar)
                    st = random.randint(TimeIntervalStart, TimeIntervalEnd)
                    log.debug(f"[{u.get_username()}] 休息 {st}s")
                    u.set_sleep_time(st)
                    webui.update_state_from_user(u)
                    webui.set_status(f"等待 {st}s 后继续...")
                if all_done:
                    log.info("=" * 50)
                    for u2 in valid_users:
                        log.info(f"[{u2.get_username()}] {u2.total_reply_success}/{u2.total_reply_sent} 成功")
                        from bark_push import push_finish; push_finish(BarkUrl, u2.get_username(), u2.total_reply_success)
                    log.info("=" * 50)
                    log.info("退出")
                    webui.set_status("所有任务完成")
                    break
                sleep_loop = random.randint(PollingMin, PollingMax)
                await asyncio.sleep(sleep_loop)
                for u in valid_users:
                    if u.get_sleep_time() > 0:
                        u.set_sleep_time(max(0, u.get_sleep_time() - sleep_loop))
                    # 每轮都更新 webui 状态（待回复数、今日统计、休眠等）
                    webui.update_state_from_user(u)
        except KeyboardInterrupt:
            log.info("收到 Ctrl+C...")
            webui.set_status("已停止")
        finally:
            incognito.stop()
            uif.stop()
            for u in valid_users:
                if u.context and not u.get_invalid():
                    await u._save_cookies()
                await u.close()
            await browser.close()
            log.info("程序结束")
            webui.set_status("已停止")
if WEB_PORT:
    run_with_web()
else:
    asyncio.run(main())
