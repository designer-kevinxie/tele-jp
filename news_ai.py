import os
import requests
from dotenv import load_dotenv
from datetime import datetime, timezone
import calendar
import cloudscraper
import feedparser
import requests

from prompt import build_prompt
from slugify import slugify


# =====================
# 路径与环境变量管理 (适配 VPS 定时任务)
# =====================
# 获取当前脚本所在的绝对路径

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BLOG_DIR = "/root/kevinx/src/content/NHK-News"

# 使用绝对路径加载 .env
env_path = os.path.join(BASE_DIR, ".env")
load_dotenv(env_path)
#加载.env之后获取API
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")





# =====================
# 获取今天日期
# =====================
today = datetime.now().strftime("%Y-%m-%d")


# =====================
# 获取 NHK RSS (增加异常捕获)
# =====================
# 注意：www3.nhk.or.jp 的 RSS 已经停止更新（一直返回旧内容），必须用 www.nhk.or.jp
url = "https://www.nhk.or.jp/rss/news/cat0.xml"  # cat0 = 主要ニュース
# 最新一条新闻超过这个天数，就认为 RSS 源已经失效
MAX_NEWS_AGE_DAYS = 2
scraper = cloudscraper.create_scraper()

try:
    # 建议设置 timeout 防止 VPS 请求无限期卡死
    response = scraper.get(url, timeout=15)
    response.raise_for_status()
except Exception as e:
    print(f"获取 NHK RSS 失败: {e}")
    exit()

feed = feedparser.parse(response.text)
if not feed.entries:
    print("没有获取到新闻")
    exit()


# =====================
# 取最新一条新闻
# =====================
def entry_time(entry):
    return getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)

# RSS 里的条目并不按时间排序，不能用固定下标，要按发布时间挑最新的
dated = [e for e in feed.entries if entry_time(e)]
if not dated:
    print("新闻条目没有发布时间，无法判断新旧")
    exit()

news = max(dated, key=entry_time)

# 源地址失效时 NHK 会一直返回同一份旧内容，这里挡住重复推送
newest = datetime.fromtimestamp(calendar.timegm(entry_time(news)), tz=timezone.utc)
age_days = (datetime.now(timezone.utc) - newest).days
if age_days > MAX_NEWS_AGE_DAYS:
    print(f"RSS 已 {age_days} 天没有更新（最新一条：{newest:%Y-%m-%d}），疑似源地址失效，本次跳过")
    exit()

title = news.title
summary = news.description
news_url = news.link
slug = slugify(title, allow_unicode=True)

print("===== 今日新闻 =====\n")
print(title)
print("\n")
print(summary)
print("\n====================\n")


# =====================
# OpenRouter api 构造
# =====================
def ask_ai(prompt):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": "deepseek/deepseek-v4-flash",
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ]
    }

    response = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers=headers,
        json=payload,
        timeout=60
    )

    response.raise_for_status()

    result = response.json()

    return result["choices"][0]["message"]["content"]


# =====================
# 构建 Prompt
# =====================
prompt = build_prompt(title, summary)

# =====================
# Gemini 分析
# =====================
try:
    ai_text = ask_ai(prompt)
except Exception as e:
    print("API 错误")
    print(e)
    exit()

# =====================
# 输出结果
# =====================
print(ai_text)

# =====================
# 生成 Markdown 文件 (使用绝对路径)
# =====================
markdown_content = f"""---
title: "{title}"
pubDate: "{today}"

tags:
  - NHK
  - Japanese
  - N1
  - News
  - English

source: "NHK"
source_url: "{news_url}"

description: "NHK Japanese learning note : {title}"
---

# 今日のNHKトップニュース

## 📰 見出し

{title}

---

{ai_text}
"""

#文件名
filename = f"{today}-nhk-news.md"
#文件路径
output_path = os.path.join(BLOG_DIR, filename)
#写入内容
with open(output_path, "w", encoding="utf-8-sig") as f:
    f.write(markdown_content)
print(f"Markdown 文件已保存：{output_path}")


# =====================
# Telegram 推送
# =====================
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
telegram_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"

with open(output_path, "rb") as file:
    try:
        response = requests.post(
            telegram_url,
            data={
                "chat_id": CHAT_ID,
                "caption": f"📰 今日 NHK 新闻\n\n{title}",
               
            },
            files={
                "document": file
            },
            timeout=200  # 增加超时限制
        )
        response.raise_for_status()
        print("Telegram 推送成功")
        
    except Exception as e:
        print("Telegram 推送失败")
        print(e)


# =====================
# 导入 N1 词汇到 100days 的 SRS 牌组
# =====================
# 整段包在 try 里：词库导入失败绝不能影响上面的新闻推送
def send_telegram_text(text):
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": text},
        timeout=60,
    ).raise_for_status()


try:
    from nhk_to_srs import import_vocab, format_summary

    srs_result = import_vocab(output_path, apply=True)
    summary = format_summary(srs_result)
    print(summary)
    send_telegram_text(summary)
except Exception as e:
    # 撞词、锁库、路径变动都走这里，只提示不中断
    print("词汇导入失败:", e)
    try:
        send_telegram_text(f"⚠️ 词汇导入失败：{e}")
    except Exception:
        pass



# =====================
# 自动 git push
# =====================


import subprocess

def git_push():
    repo_dir = "/root/kevinx"
    try:
        # 先同步远程
        subprocess.run(
            ["git", "pull", "--rebase", "origin", "master"],
            cwd=repo_dir,
            check=True
        )

        # 添加文件
        subprocess.run(
            ["git", "add", "."],
            cwd=repo_dir,
            check=True
        )

        # 检查是否有变更
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_dir,
            capture_output=True,
            text=True
        )

        if result.stdout.strip():

            subprocess.run(
                ["git", "commit", "-m", "auto: add NHK news"],
                cwd=repo_dir,
                check=True
            )

            subprocess.run(
                ["git", "push", "origin", "master"],
                cwd=repo_dir,
                check=True
            )

            print("Git push success")

        else:
            print("No changes to commit")

    except subprocess.CalledProcessError as e:
        print("Git operation failed:")
        print("Command:", e.cmd)
        print("Return code:", e.returncode)
        print("STDOUT:")
        print(e.stdout)
        print("STDERR:")
        print(e.stderr)

    except Exception as e:
        print("Unexpected error:", e)


print(os.getcwd())
# git_push()

