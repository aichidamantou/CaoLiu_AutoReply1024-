# 草榴自动回复 — 设计框架说明

> 版本: v0.25.06.18.1+

---

## 一、系统架构图

```
┌─────────────────────────────────────────────────┐
│                   主启动入口                       │
│           AutoReply.py (第973-976行)              │
│                                                   │
│   if WEB_PORT → run_with_web()  (Web模式)         │
│   else        → asyncio.run(main())  (CLI模式)    │
└──────────┬──────────────────────────┬──────────────┘
           │                          │
           ▼                          ▼
┌──────────────────┐     ┌──────────────────────────┐
│   CLI 模式 main() │     │   Web 模式 main_with_web()│
│   (第601-700行)   │     │   (第744-976行)          │
│                   │     │                           │
│  纯命令行运行      │     │  + Flask Web面板          │
│  没有 Web UI      │     │  + 时间窗控制             │
│  没有时间窗        │     │  + 多个独立后台任务        │
└──────────────────┘     └──────────────────────────┘
           │                          │
           └──────────┬───────────────┘
                      ▼
          ┌─────────────────────┐
          │  User 类 - 核心操作类 │
          │  浏览器控制 + 回复    │
          └─────────────────────┘
                      │
          ┌───────────┼───────────┬──────────────┐
          ▼           ▼           ▼              ▼
   ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
   │ 浏览器操作 │ │ Bark推送  │ │ 验证码识别│ │ 已回帖   │
   │ Playwright│ │ bark_push │ │ API打码   │ │ 持久化   │
   │ 无痕浏览  │ │ 独立模块  │ │          │ │ JSON文件  │
   │ incognito │ │          │ │          │ │          │
   └──────────┘ └──────────┘ └──────────┘ └──────────┘

┌─────────────────────────────────────────────────┐
│   WebUI (webui.py)                               │
│   Flask + SSE 日志流(主/无痕双通道) + 配置编辑    │
│   状态: state 全局字典                            │
│   + incognito_ref (读取无痕状态/日志)             │
│   + user_info_ref (读取用户信息缓存)              │
└─────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────┐
│   独立后台任务 (asyncio 同一事件循环)              │
│                                                   │
│   1. 主回复循环 (main_with_web > while True)       │
│   2. 无痕浏览 (IncognitoBrowser._run_loop)        │
│      - 自己的 Playwright 浏览器实例                │
│      - 随机指纹，独立于回复循环                    │
│      - 工作时间 8:00~23:00，随机 1~2h 一次         │
│   3. 用户信息 (UserInfoFetcher)                   │
│      - 启动时抓一次 + 回复成功后刷新               │
│      - 缓存供 WebUI 面板 + Bark 推送               │
└─────────────────────────────────────────────────┘
```

## 二、核心模块

### 2.1 主文件 `AutoReply.py`

| 模块 | 行号 | 功能 |
|------|------|------|
| 日志初始化 | 22-35 | `outputLog()` 创建文件+控制台双输出 |
| 配置加载 | 36-79 | 从 `config.yml` 读取所有配置项 |
| 验证码 | 80-111 | `apitruecaptcha` / `ttshitu` 两种打码服务 |
| **User 类** | 113-589 | **核心类**，封装所有论坛操作 |
| main() | 601-700 | CLI 模式主循环 |
| main_with_web() | 744-976 | Web 模式主循环 |
| 入口 | 973-976 | 根据 `WEB_PORT` 环境变量选择模式 |

### 2.2 独立模块

| 文件 | 类/函数 | 说明 |
|------|---------|------|
| `incognito_browser.py` | `IncognitoBrowser` | 无痕浏览定时访问，独立 Playwright 实例 |
| `user_info.py` | `UserInfoFetcher` | 从 profile.php 抓取用户信息，定时缓存 |
| `bark_push.py` | `push_reply_success()` / `push_finish()` | Bark iOS 推送（回复成功/完成） |

### 2.3 User 类（核心操作，第113-589行）

```
┌───────────────────────────────────────────┐
│                User 类                      │
├───────────────────────────────────────────┤
│  属性:                                      │
│  ├── username, password, secret            │
│  ├── context (BrowserContext)              │
│  ├── page (Page)                           │
│  ├── reply_list (待回复帖子队列)            │
│  ├── today_reply_count / daily_limit       │
│  ├── exclude_content_tids (已回帖持久化)    │
│  ├── profile (发帖/威望/金钱/贡献, bark用)  │
│  └── invalid / sleep_time                  │
├───────────────────────────────────────────┤
│  方法:                                      │
│  ├── init_context()      创建浏览器上下文   │
│  ├── close()             关闭上下文         │
│  ├── check_cookies_and_login()  登录校验    │
│  ├── get_personal_posted_list() 多页翻取    │
│  │                       已回帖(持久化文件) │
│  ├── get_today_list()    扫描帖子列表       │
│  ├── browse()            浏览帖子           │
│  ├── reply()             回复+保存已回帖    │
│  ├── like()              点赞               │
│  └── _save_replied_cache() 持久化到JSON     │
└───────────────────────────────────────────┘
```

### 2.4 WebUI（`webui.py`）

| 文件 | 功能 |
|------|------|
| `webui.py` | Flask 后端 + 状态管理 |
| `templates/index.html` | Web 前端面板（左右50%均分） |

**state 全局状态字典（第23-48行）：**
- 运行状态、用户列表、回复统计
- 无痕浏览状态（incognito_ref → incognito 对象引用）
- 用户信息缓存（user_info_ref → UserInfoFetcher 引用）
- **时间窗控制** (work_time_start/end)
- **倒计时** (user_sleep_ref + user_sleep_time 实现平滑递减)
- 今日已回复数/每日上限

## 三、数据流

### 3.1 回复一条帖子的完整流程

```
main_with_web() 主循环
  │
  ├── 1. u.get_one_link()       → 从 reply_list 随机取一个帖子 URL
  │
  ├── 2. u.browse(url)          → 用浏览器打开帖子页面
  │
  ├── 3. u.reply(url)           → 填写回复框、提交
  │       │
  │       ├── 检测每日上限
  │       ├── 填入随机回复内容 (from ReplyContent)
  │       ├── 检测失败: 禁言/未登录/每日上限/灌水预防
  │       ├── 成功 → total_reply_success++
  │       ├── 调用 push_reply_success() → Bark推送完整信息
  │       └── 保存已回帖 tid 到 replied.json
  │
  ├── 4. u.like(url)            → 随机点一个赞
  │
  ├── 5. uif.fetch(u.page)      → 刷新用户信息(每次回复后)
  │
  └── 6. set_sleep_time()       → 随机休息 (1024~2048秒)
```

### 3.2 无痕浏览流程

```
IncognitoBrowser._run_loop (独立后台任务)
  │
  ├── 检查工作时间 (8:00~23:00)
  │    └── 非工作时间 → 等待到 next_work_time
  │
  └── _visit_once(browser)
       ├── 第1步: 访问推广入口页
       ├── 第2步: 点击「按此跳转最新地址」
       ├── 第3步: 年龄验证提交表单 → 社区首页
       ├── 第4步: 随机停留 10~60s
       ├── 第5步: 刷新页面
       ├── 第5步续: 再停留 30~120s
       └── 关闭浏览器上下文
```

### 3.3 每日上限处理流程

```
reply() 检测今日已回 >= daily_limit
  │
  ├── → 返回 "daily_limit"
  │
  └── main_with_web() 收到 "daily_limit"
       ├── 计算到次日凌晨的秒数 (+960s 避开0点峰值)
       ├── set_sleep_time(seconds_to_midnight)
       └── 程序继续运行，只是休眠到第二天
```

## 四、关键设计决策

### 4.1 「不退出」策略
- **原项目的问题**：达到每日上限或没有帖子后直接 `exit(0)`，Docker 自动重启导致无限重启循环
- **我们的改进**：
  - 每日上限 → 休眠到次日重置
  - 帖子全部回复完 → `all_done = True` 正常退出

### 4.2 回复成功检测
- **原项目**：依赖 `發貼完畢點擊進入主題列表` 关键词判断成功
- **我们的改进**：论坛改版后不再返回这个关键词。改为**只检测明确失败条件**（禁言/未登录/每日上限等），只要没匹配到失败就算成功

### 4.3 平滑倒计时
- WebUI 不直接显示 `user_sleep` 的快照值
- 而是在主循环中记录 `user_sleep_ref` + `user_sleep_time`
- Flask 接口 `/api/status` 调用 `_calc_work_window()` 实时计算：`remaining = max(0, ref - (now - sleep_time))`

### 4.4 时间窗控制
- `config.yml` 中的 `WorkTimeStart` / `WorkTimeEnd` 决定回复时间段
- Web UI 面板可在线调整并持久化到 config.yml
- 非工作时间：每60秒检查一次（大幅降频），每5分钟一行日志

### 4.5 已回帖持久化
- 启动时先从 `{username}_replied.json` 加载已回帖列表
- 然后翻页抓取 `personal.php?action=post` 最多20页
- 每次回复成功后实时追加并保存

### 4.6 独立模块化
- **无痕浏览** (`incognito_browser.py`)：自己管理 Playwright 实例，独立于回复循环，随机浏览器指纹
- **用户信息** (`user_info.py`)：从 profile.php 抓取发帖/威望/金钱/贡献，启动抓一次+回复后刷新
- **Bark 推送** (`bark_push.py`)：`push_reply_success()` 发送完整用户数据

### 4.7 浏览器配置
- Cloudflare 绕过：`--disable-blink-features=AutomationControlled`
- 年龄验证绕过：拦截 `main.js` + 设置 `ismob=0` cookie
- 两步验证：支持 TOTP（pyotp）
- 验证码：支持 apitruecaptcha / ttshitu / 手动输入

## 五、Web 面板布局

```
┌────────── 50% ──────────┬────────── 50% ──────────┐
│ 📊 运行参数             │ 🕶️ 无痕参数 [+编辑]    │
│ 👤 用户信息             │   网址 / 时间窗 / 间隔  │
│   用户名(头衔)          │   停留 / 已访问 / 下次  │
│   发帖 / 威望 / 金钱   │ 🕶️ 无痕浏览日志        │
│   贡献                  │   (独立SSE流)           │
│ ⏰ 定时任务             │                         │
│ 📝 运行日志             │                         │
│  (主日志 SSE)           │                         │
│─────────────────────────│                         │
│ ● 运行中  回复统计      │                         │
└─────────────────────────┴─────────────────────────┘
```

## 六、配置说明（config.yml）

```yaml
users_config:
  - user: "用户名"
    password: "密码"
    secret: "两步验证密钥"

gobal_config:
  Host: "论坛域名"
  Fids: [7, 16]           # 回复板块
  ReplyLimit: 10          # 每日上限
  WorkTimeStart: 14       # 工作时间开始
  WorkTimeEnd: 24         # 工作时间结束
  TimeIntervalStart: 1024 # 回复间隔下限(秒)
  TimeIntervalEnd: 2048   # 回复间隔上限(秒)
  PollingMin: 60          # 轮询间隔下限
  PollingMax: 300         # 轮询间隔上限
  ScanPages: 3            # 扫前几页
  ReplyContent: [...]     # 回复内容池
  Headless: true          # 无头模式

  Incognito:              # 无痕浏览配置
    url: ""               #   推广链接
    work_start: 8         #   工作时间开始
    work_end: 23          #   工作时间结束
    interval_min: 3600    #   访问间隔下限(秒)
    interval_max: 7200    #   访问间隔上限(秒)
    stay_min: 10          #   页面停留下限(秒)
    stay_max: 60          #   页面停留上限(秒)
```

## 七、部署方式

| 方式 | 启动命令 | 说明 |
|------|---------|------|
| Docker + WebUI | `docker compose up -d` | 设置 `WEB_PORT=8888` 环境变量 |
| CLI 命令行 | `python3 AutoReply.py` | 无 Web 面板，纯粹后台 |
| Web 模式 | `WEB_PORT=8888 python3 AutoReply.py` | 带 Web 面板 |

## 八、修改记录

| 序号 | 修改内容 | 文件 |
|------|---------|------|
| 1 | 倒计时修复（基准值+时间戳） | `webui.py` |
| 2 | 状态刷新修复（每轮同步） | `AutoReply.py` |
| 3 | 回复成功检测修复 | `AutoReply.py` |
| 4 | 每日上限不死循环 | `AutoReply.py` |
| 5 | Profile正则修复 | `AutoReply.py` |
| 6 | 时间窗持久化 | `webui.py` |
| 7 | 无痕浏览独立模块 | `incognito_browser.py` 新建 |
| 8 | 用户信息独立模块 | `user_info.py` 新建 |
| 9 | Bark推送独立模块 | `bark_push.py` 新建 |
| 10 | 已回帖多页翻取+持久化 | `AutoReply.py` get_personal_posted_list() |
| 11 | SSE重复日志修复 | `templates/index.html` 重连先close |
| 12 | 移除截图功能 | `AutoReply.py` + `webui.py` |
| 13 | 左右50%均分布局 | `templates/index.html` |
