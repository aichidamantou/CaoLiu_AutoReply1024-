"""
Bark 推送独立模块
每次回复成功后推送用户信息到 iOS
"""

import requests
import logging

log = logging.getLogger('CaoLiu_AutoReply')


def bark_push(url: str, title: str, body: str = ""):
    """发送 Bark 推送"""
    if not url:
        return
    try:
        push_url = f"{url.rstrip('/')}/{title}/{body}"
        requests.get(push_url, timeout=5)
    except Exception as e:
        log.debug(f"Bark 推送失败: {e}")


def push_reply_success(bark_url: str, username: str, total_reply: int,
                       posts: str = "?", pres: str = "?", usd: str = "?",
                       contribution: str = "?"):
    """回复成功时推送完整用户信息"""
    if not bark_url:
        return
    title = f"✅ {username} 回复成功"
    body = (
        f"已回复: {total_reply}次 | "
        f"发帖: {posts} | 威望: {pres} | "
        f"金钱: {usd} | 贡献: {contribution}"
    )
    bark_push(bark_url, title, body)


def push_finish(bark_url: str, username: str, total_reply: int):
    """全部完成推送"""
    if not bark_url:
        return
    bark_push(bark_url, f"🏁 {username} 本轮{total_reply}帖")
