import cloudscraper
import feedparser

# RSS 地址
url = "https://www3.nhk.or.jp/rss/news/cat0.xml"

# 创建 scraper
scraper = cloudscraper.create_scraper()

# 获取 XML
response = scraper.get(url)

print("状态码:", response.status_code)

# XML 内容
xml_content = response.text

print("XML长度:", len(xml_content))

# 解析 XML
feed = feedparser.parse(xml_content)

print("新闻数量:", len(feed.entries))

print("\n===== 今日 NHK 新闻 =====\n")

for news in feed.entries[:5]:

    print("标题：")
    print(news.title)

    print("\n摘要：")
    print(news.description)

    print("\n链接：")
    print(news.link)

    print("\n发布时间：")
    print(news.published)

    print("\n" + "=" * 60 + "\n")