"""把 tele-jp 生成的 NHK 学习笔记里的 N1 词汇导入 100days 的 SRS 牌组。

设计要点（对齐 100days 的 src/app/actions-srs.ts::addCard）：
- 查重用 NFKC 规范化后的 front，跨牌组比对；命中不自动合并，跳过并交给人决定
- FSRS 初始状态等价于 ts-fsrs createEmptyCard：除 due 外全部取列默认值，不伪造数值
- origin='manual' 配合 card_encounters(source='reading')，不占预装词库每日新卡额度
- 首次复习时间为导入时刻，即当天就进队列。这里刻意不用 addCard 的
  startTomorrow：那条默认假设「你已经在别处学过了」，而自动导入发生在
  你读新闻之前，app 里才是第一次接触

既可当脚本跑（默认 dry-run，--apply 才写库），也可由 news_ai.py 导入 import_vocab()。
"""
import argparse
import os
import re
import sqlite3
import sys
import unicodedata
from datetime import datetime, timedelta, timezone

DB = os.getenv("DAYS100_DB", "/root/apps/100days/data/app.db")
DECK_ID = int(os.getenv("DAYS100_DECK_ID", "3"))
EXAM_ID = int(os.getenv("DAYS100_EXAM_ID", "1"))
PLAN_ID = int(os.getenv("DAYS100_PLAN_ID", "1"))
TZ = timezone(timedelta(hours=9))  # Asia/Tokyo

FIELDS = ["単語", "読み方", "意味", "中文", "英文", "例句", "翻译"]


def norm(s: str) -> str:
    """等价于 actions-srs.ts 的 normalizeCardText"""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", s).strip())


def parse_vocab(md: str):
    """从 '# 2. N1 重点词汇' 段落解析词条"""
    m = re.search(r"#\s*2\.[^\n]*\n(.*?)(?=\n#\s*3\.)", md, re.S)
    if not m:
        return []
    out = []
    for chunk in re.split(r"\n-{3,}\n", m.group(1)):
        d = {}
        for f in FIELDS:
            mm = re.search(rf"^-\s*{f}[：:]\s*(.+)$", chunk, re.M)
            if mm:
                d[f] = mm.group(1).strip()
        if d.get("単語"):
            out.append(d)
    return out


def first_study_ms() -> int:
    """当天就可以复习：导入时刻即首次可进队列时间"""
    return int(datetime.now(TZ).timestamp() * 1000)


def resolve_day(con, date_str: str):
    """按日历日期反查 100days 的 Day，拿不到就返回 (None, None)"""
    row = con.execute(
        "SELECT id, day_index FROM plan_days WHERE plan_id=? AND date=?",
        (PLAN_ID, date_str),
    ).fetchone()
    return (row[0], row[1]) if row else (None, None)


def import_vocab(markdown_path: str, deck_id: int = DECK_ID, day_index=None, apply=False):
    """返回 {'new': [...], 'dup': [(词, [(front, 牌组)])], 'day_index': n, 'written': n}"""
    with open(markdown_path, encoding="utf-8-sig") as f:
        items = parse_vocab(f.read())
    result = {"new": [], "dup": [], "day_index": day_index, "written": 0,
              "parsed": len(items)}
    if not items:
        return result

    con = sqlite3.connect(DB)
    try:
        if day_index is None:
            _, day_index = resolve_day(con, datetime.now(TZ).strftime("%Y-%m-%d"))
            result["day_index"] = day_index
        day_id, _ = (resolve_day(con, datetime.now(TZ).strftime("%Y-%m-%d"))
                     if day_index is not None else (None, None))

        existing = {}
        for cid, front, deck_name in con.execute(
            "SELECT c.id, c.front, d.name FROM cards c JOIN decks d ON d.id=c.deck_id"
            " WHERE c.exam_id=?", (EXAM_ID,)
        ):
            existing.setdefault(norm(front), []).append((front, deck_name))

        for it in items:
            key = norm(it["単語"])
            if key in existing:
                result["dup"].append((it["単語"], existing[key]))
            else:
                result["new"].append(it)

        if not apply or not result["new"]:
            return result

        due = first_study_ms()
        cur = con.cursor()
        cur.execute("BEGIN")
        try:
            for it in result["new"]:
                back = "・".join(x for x in (it.get("意味"), it.get("中文")) if x)
                cur.execute(
                    "INSERT INTO cards (exam_id, deck_id, type, front, reading, back,"
                    " back_alt, tags, source_day_index, origin)"
                    " VALUES (?,?,'word',?,?,?,?,'[]',?,'manual')",
                    (EXAM_ID, deck_id, it["単語"], it.get("読み方"), back,
                     it.get("英文"), day_index),
                )
                cid = cur.lastrowid
                if it.get("例句"):
                    cur.execute(
                        "INSERT INTO card_examples (card_id, ja, translation, sort_order)"
                        " VALUES (?,?,?,0)",
                        (cid, it["例句"], it.get("翻译")),
                    )
                # 等价于 initCardState()：除 due 外走列默认值，不伪造 FSRS 数值
                cur.execute(
                    "INSERT INTO card_states (card_id, due) VALUES (?,?)", (cid, due)
                )
                cur.execute(
                    "INSERT INTO card_encounters (exam_id, card_id, plan_day_id, source,"
                    " context, first_review_at) VALUES (?,?,?,'reading',?,?)",
                    (EXAM_ID, cid, day_id, it.get("例句", ""), due),
                )
            con.commit()
            result["written"] = len(result["new"])
        except Exception:
            con.rollback()
            raise
    finally:
        con.close()
    return result


def format_summary(r) -> str:
    """给 Telegram 用的简短汇报"""
    if r["parsed"] == 0:
        return "⚠️ 词汇导入：没有解析到词条"
    lines = [f"📚 词汇导入：新增 {r['written']} / 解析 {r['parsed']}"]
    if r["day_index"] is not None:
        lines[0] += f"（Day {r['day_index']}）"
    if r["new"] and r["written"]:
        lines.append("　" + "、".join(i["単語"] for i in r["new"]))
    for word, hits in r["dup"]:
        where = "、".join(f"{f}（{d}）" for f, d in hits[:2])
        lines.append(f"⚠️ 已存在，跳过：{word} → {where}　需手动确认是否另建词义")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("markdown")
    ap.add_argument("--deck-id", type=int, default=DECK_ID)
    ap.add_argument("--day-index", type=int, default=None)
    ap.add_argument("--apply", action="store_true", help="不加则只做 dry-run")
    a = ap.parse_args()
    r = import_vocab(a.markdown, a.deck_id, a.day_index, a.apply)
    print(format_summary(r))
    if not a.apply:
        print("(dry-run，未写库)")
    if r["parsed"] == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
