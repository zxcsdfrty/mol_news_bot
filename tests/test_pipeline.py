from datetime import datetime, timedelta, timezone

import pytest

from news_bot import fetcher
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


# 2026-09-17 實際抓到的同一事件標題，各家改寫幅度極大。
# 修正前 14 則全部各推一封，等於同一件事洗版 14 次。
_婚假新聞 = [
    "婚假再加碼！勞動部擬「8天延至14天」 新制上路時間曝",
    "婚假大紅包來了！勞動部將預告婚假8天增至14天",
    "拯救低迷生育率！勞動部擬婚假8天延為14天 網議論：解決高房價才是關鍵",
    "婚假8→14天「6天薪水政府補」 勞動部預告將修法",
    "婚假8天增至14天！勞動部今將公布新制上路時間",
    "婚假要變14天了！勞動部預告10/1上路 「這6天」薪資政府補助",
    "勞動部宣布「婚假8天變14天」10月上路 已登記者還有機會！條件一次看",
    "婚假8天變14天 勞動部宣布10月1日上路",
    "勞動部修法新制10/1上路！婚假升級14天「已婚也能補假」不扣薪",
    "勞動部預告婚假由8天增至14天 10月上路",
    "結婚大禮包！ 勞動部：婚嫁延長至14天擬10/1上路",
    "婚假8天加碼為14天！勞動部：10/1上路、6天薪資負擔由政府支應",
]

# 彼此無關的事件，不得被併在一起
_不同事件 = [
    "一早「媽媽領補助」！入帳4.2萬 勞動部月底前再發15筆",
    "雲林母親載雙胞胎上學釀1死2重傷 逃逸移工撞出一堆問題引議會關切",
    "外送專法逼漲價2／勞動部直言現無修法理由 外送平台現有經驗恐打掉重練！",
    "勞保年金65歲才領吃虧了！專家試算「男女最佳請領年齡」",
    "洪申翰為國際技能競賽53國手授旗：賽場外的事情交給我們",
    "基本工資上看3萬？卓榮泰曝關鍵：由最低工資審議會決定",
    "最多領500萬！企托補助申請倒數2週 勞動部籲雇主把握機會",
]


def test_dedupe_same_event_real_titles():
    """同一事件的多家改寫應大幅收斂，且不得併掉無關事件。"""
    items = [_item(t, f"https://x/{n}", summary="摘要內容" * 10)
             for n, t in enumerate(_婚假新聞 + _不同事件)]
    kept = dedupe_batch(items)
    titles = [i.title for i in kept]

    婚假剩 = sum(1 for t in titles if t in _婚假新聞)
    assert 婚假剩 <= 4, f"同一事件仍推播 {婚假剩} 則：{titles}"
    # 無關事件一則都不能少
    for t in _不同事件:
        assert t in titles, f"不同事件被誤併：{t}"


def test_dedupe_chains_through_middle_title():
    """A 與 C 不相似，但兩者都像 B 時，三則應收斂為同一群。"""
    a = "婚假再加碼！勞動部擬「8天延至14天」 新制上路時間曝"
    b = "婚假8天增至14天！勞動部今將公布新制上路時間"
    c = "婚假大紅包來了！勞動部將預告婚假8天增至14天"
    assert not similar(title_key(a), title_key(c))   # 直接比對不相似
    items = [_item(t, f"https://x/{n}", summary="摘要" * 20)
             for n, t in enumerate((a, b, c))]
    assert len(dedupe_batch(items)) == 1


def test_dedupe_keeps_short_titles_apart():
    """去掉機關名後過短者不得僅因零星字元相符就被併掉。"""
    items = [_item(t, f"https://x/{n}") for n, t in enumerate(
        ("勞動部說明", "勞動部回應", "勞動部澄清"))]
    assert len(dedupe_batch(items)) == 3


def test_is_excluded():
    """排除社群來源，但網域比對不可誤傷正常媒體。"""
    ex = ["facebook.com", "youtube.com", "x.com", "ptt.cc"]
    assert fetcher.is_excluded("facebook.com", "https://m.facebook.com/p/1", ex)
    assert fetcher.is_excluded("Facebook", "https://news.google.com/rss/articles/A", ex)
    assert fetcher.is_excluded("", "https://www.youtube.com/watch?v=1", ex)
    # x.com 不可誤傷 xxx.com.tw、chinatimes.com 等正常網域
    assert not fetcher.is_excluded("中時新聞網", "https://www.chinatimes.com/a", ex)
    assert not fetcher.is_excluded("某報", "https://www.xxx.com.tw/a", ex)
    assert not fetcher.is_excluded("中央社", "https://www.cna.com.tw/news/1", ex)
    # 品牌名過短者（x.com 的 x）不得用於名稱比對，否則會命中任意媒體名
    assert not fetcher.is_excluded("Taiwan News Express", "https://example.com.tw/a", ex)


def test_source_tier():
    """官方 RSS > 其他原始媒體 > 聚合轉載平台。"""
    assert fetcher.source_tier("中央社") == 0
    assert fetcher.source_tier("自由時報") == 0
    assert fetcher.source_tier("TVBS新聞網") == 1
    assert fetcher.source_tier("民視新聞網") == 1
    assert fetcher.source_tier("LINE TODAY") == 2
    assert fetcher.source_tier("Yahoo新聞") == 2
    assert fetcher.source_tier("CMoney") == 2
    assert fetcher.source_tier("") == 1


def test_split_outlet():
    """聚合平台把原始媒體名接在標題尾端，應取回當作真正的來源。"""
    # 2026-09-17 實際案例
    assert fetcher.split_outlet(
        "洪申翰出訪「恐被當中國人」？本人嚴正駁斥 | 民視新聞網", "LINE TODAY"
    ) == ("洪申翰出訪「恐被當中國人」？本人嚴正駁斥", "民視新聞網")
    assert fetcher.split_outlet(
        "洪申翰為國際技能競賽53國手授旗 | Newtalk", "LINE TODAY"
    ) == ("洪申翰為國際技能競賽53國手授旗", "Newtalk")

    # 「產業」是分類名不是媒體名，標題要清掉但來源不可改
    title, source = fetcher.split_outlet(
        "婚假要變14天了！勞動部預告10/1上路| 產業", "LINE TODAY")
    assert title == "婚假要變14天了！勞動部預告10/1上路"
    assert source == "LINE TODAY"

    # 來源本來就是原始媒體時不更動來源
    assert fetcher.split_outlet("勞動部公布基本工資 | 財經", "中央社")[1] == "中央社"
    # 沒有尾綴時原樣回傳
    assert fetcher.split_outlet("勞動部公布基本工資", "中央社") == ("勞動部公布基本工資", "中央社")
    # 整個標題都是尾綴時不可清成空字串
    assert fetcher.split_outlet("| 民視新聞網", "LINE TODAY") == ("| 民視新聞網", "LINE TODAY")


def test_dedupe_prefers_original_outlet_over_aggregator():
    """同一事件同時有原始媒體與聚合平台版本時，留原始媒體那一則。"""
    items = [
        _item("勞動部宣布婚假8天變14天 10月1日上路", "https://x/1", "LINE TODAY",
              summary="摘要內容" * 10),
        _item("勞動部宣布婚假8天變14天 10月1日上路", "https://x/2", "中央社",
              summary="摘要內容" * 10),
    ]
    out = dedupe_batch(items)
    assert len(out) == 1 and out[0].source == "中央社"


def test_redact_token():
    """日誌不得洩漏 Bot Token（公開 repo 的 Actions 紀錄任何人都看得到）。"""
    token = "123456789:AAFakeTokenForTestOnly"  # noqa: S105 假 token，僅供測試
    object.__setattr__(notifier.settings, "telegram_bot_token", token)
    try:
        msg = f"HTTPSConnectionPool: Max retries exceeded with url: /bot{token}/sendMessage"
        assert token not in notifier._redact(msg)
        assert "***" in notifier._redact(msg)
    finally:
        object.__setattr__(notifier.settings, "telegram_bot_token", "")
    # token 為空字串時不可把每個字元都換掉
    assert notifier._redact("連線逾時") == "連線逾時"


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
