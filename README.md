# CaoLiu AutoReply - 草榴自动回复

> 基于 [0honus0/CaoLiu_AutoReply](https://github.com/0honus0/CaoLiu_AutoReply) 改进重构

## ✨ 特性

- **🤖 自动回复** — Playwright 浏览器自动化，每日定时回复指定板块帖子
- **🕐 时间窗配置** — 设置每日工作时间段，到点自动停止/恢复
- **🔄 每日上限不死循环** — 达上限后休眠到次日重置，不退出
- **📊 运行报告** — 统计发帖/威望/金钱/贡献，Bark iOS 推送
- **🕶️ 无痕浏览** — 定时用无痕浏览器模拟真人访问推广链接，赚取贡献值
  - 随机 UA/视口/语言/请求头，每次不同浏览器指纹
  - 完整访问流程：入口页→跳转→年龄验证→社区浏览→刷新
- **👤 用户信息面板** — 实时显示用户名/头衔/发帖/威望/金钱/贡献
- **🌐 Web 管理面板** — 左右分栏，参数调整 + 日志流 + 无痕配置编辑
- **⏱️ 平滑倒计时** — 实时显示回复倒计时和时间窗状态
- **🔔 Bark 推送** — 回复成功后推送用户数据
- **🛡️ 容错增强** — 登录重试、每日限额保护、多页翻取已回帖（持久化）

## 🚀 部署

```bash
docker-compose up -d
```

Web 面板: `http://localhost:8888`

## 📦 配置文件

`config.yml` 已添加 `.gitignore`，不会误提交私人信息。

### 主要配置项

| 配置段 | 说明 |
|--------|------|
| `users_config` | 论坛账号密码（支持多用户） |
| `gobal_config.Incognito` | 无痕浏览配置（网址/时间窗/间隔/停留） |
| `gobal_config.WorkTimeStart/End` | 回复工作时间窗 |
| `gobal_config.Fids` | 回复板块ID列表 |
| `gobal_config.ReplyContent` | 回复内容池 |
| `gobal_config.BarkUrl` | iOS 推送 URL |

## 🧩 模块结构

| 模块 | 说明 |
|------|------|
| `AutoReply.py` | 主程序（纯 CLI / Web UI 双模式） |
| `webui.py` | Flask Web 管理界面 |
| `templates/index.html` | Web 面板前端（左右分栏） |
| `incognito_browser.py` | 无痕浏览独立模块 |
| `user_info.py` | 用户信息抓取独立模块 |
| `bark_push.py` | Bark iOS 推送独立模块 |

## 📝 声明

本项目仅供学习交流，请遵守当地法律法规。
