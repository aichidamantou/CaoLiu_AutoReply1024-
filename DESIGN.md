# 草榴自动回复 — 设计框架说明

> 基于 `0honus0/CaoLiu_AutoReply` 改进
> 版本: v0.25.06.18.1

---

## 一、系统架构图

```
┌─────────────────────────────────────────────────┐
│                   主启动入口                       │
│           AutoReply.py (第1009-1012行)            │
│                                                   │
│   if WEB_PORT → run_with_web()  (Web模式)         │
│   else        → asyncio.run(main())  (CLI模式)    │
└──────────┬──────────────────────────┬──────────────┘
           │                          │
           ▼                          ▼
┌──────────────────┐     ┌──────────────────────────┐
│   CLI 模式 main() │     │   Web 模式 main_with_web()│
│   (第601-734行)   │     │   (第744-1008行)          │
│                   │     │                           │
│  纯命令行运行      │     │  + Flask Web面板          │
│  没有 Web UI      │     │  + 截图循环               │
│  没有时间窗        │     │  + 时间窗控制             │
│  没有截图          │     │  + 实时状态更新           │
└──────────────────┘     └──────────────────────────┘
           │                          │
           └──────────┬───────────────┘
                      ▼
          ┌─────────────────────┐
          │  User 类 - 核心操作类 │
          │  浏览器控制 + 回复    │
          └─────────────────────┘
                      │
          ┌───────────┼───────────┐
          ▼           ▼           ▼
   ┌──────────┐ ┌──────────┐ ┌──────────┐
   │ 浏览器操作 │ │ Bark推送  │ │ 验证码识别│
   │ Playwright│ │ iOS通知   │ │ API打码   │
   └──────────┘ └──────────┘ └──────────┘

┌─────────────────────────────────────────────────┐
│               WebUI (webui.py)                   │
│   Flask + SSE 日志流 + 实时截图 + 配置编辑       │
│   状态: state 全局字典 (第23-48行)               │
└─────────────────────────────────────────────────┘
```

## 二、核心模块

### 2.1 主文件 `AutoReply.py`

| 模块 | 行号 | 功能 |
|------|------|------|
| 日志初始化 | 22-35 | `outputLog()` 创建文件+控制台双输出 |
| 配置加载 | 36-79 | 从 `config.yml` 读取所有配置项 |
| 验证码 | 80-111 | `apitruecaptcha` / `ttshitu` 两种打码服务 |
| Bark推送 | 112-121 | 发送 iOS 推送通知 |
| **User 类** | 122-599 | **核心类**，封装所有论坛操作 |
| main() | 601-734 | CLI 模式主循环 |
| main_with_web() | 744-1008 | Web 模式主循环 |
| 入口 | 1009-1012 | 根据 `WEB_PORT` 环境变量选择模式 |

### 2.2 User 类（核心操作，第122-599行）

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
│  ├── exclude_content_tids (已回/版主列表)   │
│  ├── _profile_cache (发帖/威望/金钱)        │
│  └── invalid / sleep_time                  │
├───────────────────────────────────────────┤
│  方法:                                      │
│  ├── init_context()      创建浏览器上下文   │
│  ├── close()             关闭上下文         │
│  ├── check_cookies_and_login()  登录校验    │
│  ├── get_today_list()    扫描帖子列表       │
│  ├── browse()            浏览帖子           │
│  ├── reply()             回复帖子           │
│  ├── like()              点赞               │
│  └── get_user_info()     查询用户信息       │
└───────────────────────────────────────────┘
```

### 2.3 WebUI（`webui.py`）

| 文件 | 功能 |
|------|------|
| `webui.py` | Flask 后端 + 状态管理 |
| `templates/index.html` | Web 前端面板 |

**state 全局状态字典（第23-48行）：**
- 运行状态、用户列表、回复统计
- 截图缓存、最后一次回复内容
- **时间窗控制** (work_time_start/end)
- **倒计时** (user_sleep_ref + user_sleep_time 实现平滑递减)
- 今日已回复数/每日上限

## 三、数据流

### 3.1 回复一条帖子的完整流程

```
main() 主循环
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
  │       └── 成功 → total_reply_success++
  │
  ├── 4. u.like(url)            → 随机点一个赞
  │
  ├── 5. bark_push()            → iOS 推送通知
  │
  └── 6. set_sleep_time()       → 随机休息 (1024~2048秒)
```

### 3.2 每日上限处理流程

```
reply() 检测今日已回 >= daily_limit
  │
  ├── → 返回 "daily_limit"
  │
  └── main() 收到 "daily_limit"
       ├── 计算到次日凌晨的秒数 (+960s 避开0点峰值)
       ├── set_sleep_time(seconds_to_midnight)
       └── 程序继续运行，只是休眠到第二天
```

## 四、关键设计决策

### 4.1 「不退出」策略
- **原项目的问题**：达到每日上限或没有帖子后直接 `exit(0)`，Docker 自动重启导致无限重启循环
- **我们的改进**：
  - 每日上限 → 休眠到次日重置（第679-685行）
  - 帖子全部回复完 → `all_done = True` 正常退出（第706-718行）

### 4.2 回复成功检测
- **原项目**：依赖 `發貼完畢點擊進入主題列表` 关键词判断成功
- **我们的改进**：论坛改版后不再返回这个关键词。改为**只检测明确失败条件**（禁言/未登录/每日上限等），只要没匹配到失败就算成功（第486行）

### 4.3 平滑倒计时
- WebUI 不直接显示 `user_sleep` 的快照值
- 而是在主循环中记录 `user_sleep_ref` + `user_sleep_time`（第648-649行）
- Flask 接口 `/api/status` 调用 `_calc_work_window()` 实时计算：`remaining = max(0, ref - (now - sleep_time))`（webui.py 第129-131行）

### 4.4 时间窗控制
- `config.yml` 中的 `WorkTimeStart` / `WorkTimeEnd` 决定运行时间段
- Web UI 面板可在线调整并持久化到 config.yml
- 非工作时间：每15秒检查一次（第912行），显示倒计时
- 工作时间：正常回复操作

### 4.5 浏览器配置
- Cloudflare 绕过：`--disable-blink-features=AutomationControlled`（第623行）
- 年龄验证绕过：拦截 `main.js` + 设置 `ismob=0` cookie（第161-172行）
- 两步验证：支持 TOTP（pyotp，第261行）
- 验证码：支持 apitruecaptcha / ttshitu / 手动输入

## 五、配置说明（config.yml）

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
  BarkUrl: ""             # iOS推送
  Like: true              # 是否点赞
  Forbid: true            # 是否屏蔽版主
```

## 六、部署方式

| 方式 | 启动命令 | 说明 |
|------|---------|------|
| Docker + WebUI | `docker compose up -d` | 设置 `WEB_PORT=8888` 环境变量 |
| CLI 命令行 | `python3 AutoReply.py` | 无 Web 面板，纯粹后台 |
| Web 模式 | `WEB_PORT=8888 python3 AutoReply.py` | 带 Web 面板 |

## 七、修改记录

| 序号 | 修改内容 | 文件位置 |
|------|---------|---------|
| 1 | 倒计时修复（基准值+时间戳） | `webui.py` 第129-131行 `_calc_work_window()` |
| 2 | 状态刷新修复（每轮同步） | `AutoReply.py` 第995-996行 |
| 3 | 回复成功检测修复 | `AutoReply.py` 第486行 |
| 4 | 推送优化（profile缓存） | `AutoReply.py` 第556-575行 `_profile_cache` |
| 5 | 每日上限不死循环 | `AutoReply.py` 第679-685行 |
| 6 | Profile正则修复 | `AutoReply.py` 第561-572行 |
| 7 | 时间窗持久化 | `webui.py` 第267-278行 |
| 8 | Bark推送格式修改 | `AutoReply.py` 第494-495行 |
