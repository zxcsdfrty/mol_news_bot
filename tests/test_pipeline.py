from datetime import datetime, timedelta, timezone

import pytest

from news_bot import main as main_mod
from news_bot import notifier
from news_bot.classifier import Classifier
from news_bot.dedup import dedupe_batch, similar, title_key
from news_bot.models import NewsItem
from news_bot.storage import SQLiteStore

TW = timezone(timedelta(hours=8))


@pytest.fixture(scope="module")
def clf():
    return Classifier()


@pytest.mark.parametrize("title,summary,expected", [
    ("勞動部公布最新失業率", "", True),
    ("洪申翰：明年基本工資將調漲", "", True),
    ("勞保年金改革 立委砲轟撥補不足", "勞保基金恐破產", True),        # 無機關名，但 2 個議題詞
    ("工地墜落意外 一死一傷 職安署勒令停工", "", True),
    ("美國勞動部公布非農就業數據優於預期", "", False),               # 外國機關排除
    ("韓國雇用勞動部推動週休四日", "", False),
    ("美國勞動部數據出爐 我國勞動部：持續觀察", "", True),           # 排除詞外仍有本部
    ("藝人自曝月薪僅三萬", "", False),                              # 單一泛用詞不收
    ("颱風假怎麼放？人事總處今晚宣布", "", False),
    ("NBA 季後賽 湖人逆轉勝", "", False),
])
def test_relevance(clf, title, summary, expected):
    assert clf.classify(title, summary).relevant is expected


def test_topics_and_sentiment(clf):
    r = clf.classify("移工仲介費爭議 勞動部遭批評", "直聘制度成效受質疑")
    assert r.topics[0] == "移工"
    assert r.sentiment == "負面"
    r = clf.classify("勞動部加碼育嬰留職停薪津貼 明年上路", "")
    assert "性別平等與育兒" in r.topics
    assert r.sentiment == "正面"


def test_title_key_and_similar():
    a = title_key("【獨家】勞動部宣布：基本工資明年調漲至3萬元")
    b = title_key("勞動部宣布基本工資明年調漲至3萬元（影）")
    assert a == b
    assert similar(title_key("勞動部宣布基本工資明年調漲至3萬元"),
                   title_key("勞動部宣布 明年基本工資調漲至3萬元"))
    assert similar(title_key("勞動部宣布基本工資調漲"), title_key("基本工資調漲 勞動部今宣布"))
    for x, y in [("勞動部宣布基本工資調漲", "職安署勒令工地停工"),
                 ("勞動部公布最新失業率", "勞動部公布基本工資"),
                 ("勞動部長洪申翰今視察桃園就業中心", "勞動部長洪申翰今出席移工節活動"),
                 ("勞保局：老年給付申請再創新高", "勞動部宣布基本工資調漲")]:
        assert not similar(title_key(x), title_key(y)), (x, y)


def _item(title, url, source="中央社", summary="", hours=1):
    return NewsItem(title=title, url=url, source=source, summary=summary,
                    published_at=datetime.now(TW) - timedelta(hours=hours))


def test_dedupe_prefers_publisher_url():
    items = [
        _item("勞動部宣布基本工資調漲", "https://news.google.com/rss/articles/x", "ETtoday"),
        _item("勞動部宣布基本工資調漲", "https://www.cna.com.tw/1", summary="摘要內容" * 10),
        _item("勞動部宣布基本工資調漲", "https://www.cna.com.tw/1", summary="摘要內容" * 10),
    ]
    out = dedupe_batch(items)
    assert len(out) == 1 and "cna" in out[0].url


def test_message_format(monkeypatch):
    """廠商格式：一則新聞一封訊息，標題後標來源，裸網址供 Telegram 產生預覽卡片。"""
    rows = [{"id": i, "title": f"勞動部新聞標題 {i} <測試&跳脫>", "summary": "摘" * 400,
             "url": f"https://x/{i}?a=1&b=2", "source": "中央社",
             "published_at": datetime.now(TW).isoformat(), "topics": ["勞工保險"],
             "sentiment": "負面"} for i in range(40)]
    msgs = notifier.build_messages(rows)
    assert len(msgs) == 40                                  # 不再合併成多則新聞一封
    assert all(len(ids) == 1 for _, ids in msgs)
    assert sorted(i for _, ids in msgs for i in ids) == list(range(40))
    assert all(len(m) <= 4096 for m, _ in msgs)

    lines = msgs[0][0].split("\n")
    assert lines[0] == "【新聞通報】"
    assert lines[1].endswith(" (中央社)</b>")                # 標題後方標示來源
    assert lines[2] == "https://x/0?a=1&amp;b=2"            # 裸網址，不做成超連結
    assert lines[3].startswith("　")                         # 摘要以全形空格縮排
    assert "&lt;測試&amp;跳脫&gt;" in lines[1]               # HTML 跳脫
    assert "摘" * 300 in lines[3] and "摘" * 301 not in lines[3]  # 摘要截斷於上限
    # 議題與輿情傾向只進資料庫，不進推播訊息
    assert "🔴" not in msgs[0][0] and "勞工保險" not in msgs[0][0]


def test_message_format_optional_fields():
    """缺少來源或摘要時，不應留下多餘的括號或空行。"""
    rows = [{"id": 1, "title": "勞動部公布基本工資", "summary": "", "url": "https://x/1",
             "source": ""}]
    text, ids = notifier.build_messages(rows)[0]
    assert text == "【新聞通報】\n<b>勞動部公布基本工資</b>\nhttps://x/1"
    assert ids == [1]


def test_quiet_hours():
    at = lambda h: datetime(2026, 9, 17, h, tzinfo=TW)  # noqa: E731
    assert main_mod.in_quiet_hours(at(23), "23-7")
    assert main_mod.in_quiet_hours(at(3), "23-7")
    assert not main_mod.in_quiet_hours(at(7), "23-7")
    assert main_mod.in_quiet_hours(at(13), "12-14")
    assert not main_mod.in_quiet_hours(at(13), "")


def test_end_to_end(tmp_path, monkeypatch):
    batches = [
        [
            _item("勞動部宣布基本工資調漲", "https://a/1", summary="月薪與時薪同步調整" * 3),
            _item("勞動部宣布基本工資調漲！", "https://news.google.com/x1", "TVBS"),
            _item("NBA 湖人逆轉勝", "https://a/2"),
            _item("職安署：工地墜落意外 勒令停工", "https://a/3", "自由時報", summary="職災" * 20),
        ],
        [   # 第二輪：他台報導同一事件 + 一則新新聞
            _item("基本工資調漲 勞動部今宣布", "https://b/1", "聯合新聞網", summary="x" * 40),
            _item("勞保局：老年給付申請再創新高", "https://b/2", "經濟日報", summary="y" * 40),
        ],
    ]
    sent_batches = []
    monkeypatch.setattr(main_mod, "fetch_all", lambda: batches.pop(0))
    monkeypatch.setattr(main_mod, "enrich_summary", lambda i: None)
    monkeypatch.setattr(main_mod, "push", lambda rows, dry_run=False:
                        sent_batches.append([r["title"] for r in rows]) or [r["id"] for r in rows])
    object.__setattr__(main_mod.settings, "min_push_interval", 0)

    store = SQLiteStore(tmp_path / "t.db")
    s1 = main_mod.run(store=store)
    assert s1["relevant"] == 3 and s1["unique"] == 2 and s1["pushed"] == 2

    s2 = main_mod.run(store=store)
    assert s2["dups"] == 1 and s2["pushed"] == 1
    assert sent_batches[1] == ["勞保局：老年給付申請再創新高"]
    assert store.pending() == []
