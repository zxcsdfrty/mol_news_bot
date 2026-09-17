"""關鍵字調校小工具：輸入標題（與摘要），顯示分數、議題與判斷結果。

    python -m news_bot.check "勞保年金改革 立委砲轟" "勞保基金恐在2031年破產"
"""
import sys

from .classifier import Classifier


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return
    title = sys.argv[1]
    summary = sys.argv[2] if len(sys.argv) > 2 else ""
    clf = Classifier()
    r = clf.classify(title, summary)
    print(f"標題：{title}")
    print(f"分數：{r.score}（門檻 {clf.threshold}）→ {'✅ 收錄' if r.relevant else '❌ 不收錄'}")
    print(f"議題：{'、'.join(r.topics) or '-'}")
    print(f"命中：{'、'.join(r.matched) or '-'}")
    print(f"傾向：{r.sentiment}")


if __name__ == "__main__":
    main()
