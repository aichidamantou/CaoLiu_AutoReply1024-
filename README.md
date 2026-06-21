# CaoLiu AutoReply - 草榴自动回复

> 基于 [0honus0/CaoLiu_AutoReply](https://github.com/0honus0/CaoLiu_AutoReply) 改进重构

## ✨ 主要改进

- **🕐 时间窗配置** — 每日工作时间段可配置（Web UI 在线调整）
- **📊 运行报告** — 统计发帖/威望/金钱，Bark iOS 推送
- **🔄 每日上限不死循环** — 达上限后休眠到次日重置，非退出
- **⏱️ 平滑倒计时** — 状态栏实时显示剩余等待时间
- **🔔 推送优化** — 回复成功/运行报告/任务结束推送
- **🌐 Web 面板** — 参数调整 + 实时日志 + 截图 + 配置编辑
- **🛡️ 容错增强** — 登录重试、回复检测、Profile 解析

## 🚀 部署

```bash
docker-compose up -d
```

Web 面板: `http://localhost:8888`

## 📦 配置

编辑 `config.yml` 填入你的论坛账号密码。

## 📝 声明

本项目仅供学习交流，请遵守当地法律法规。
