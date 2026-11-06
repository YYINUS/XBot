from datetime import datetime, timezone

from xbot.models import Item
from xbot.sources import defillama, rss
from xbot.store import Store
from xbot.textutil import lint_post, weighted_len
from xbot.x_client import load_archive


def test_weighted_len_urls_and_cjk():
    assert weighted_len("hello") == 5
    assert weighted_len("see https://example.com/a/very/long/path/that/is/long") == 4 + 23
    assert weighted_len("代币化") == 6


def test_lint_flags_banned_links_and_length():
    issues = lint_post("NFA but this is huge 🚀 https://x.co", max_chars=280, banned=["NFA", "🚀"],
                       max_hashtags=1, max_emojis=1)
    assert any("NFA" in i for i in issues)
    assert any("🚀" in i for i in issues)
    assert "contains a link" in issues
    assert lint_post("a" * 281, max_chars=280, banned=[], max_hashtags=1, max_emojis=1) == ["too long: 281/280"]
    # word-boundary: 'nfa' inside another word is fine
    assert lint_post("infant industry", max_chars=280, banned=["NFA"], max_hashtags=1, max_emojis=1) == []


def test_store_dedupes_by_url_and_title(tmp_path):
    st = Store(tmp_path / "db.sqlite")
    a = Item(source="A", title="Ondo launches USDY on Base", url="https://a.com/x?utm=1")
    b = Item(source="B", title="Ondo launches USDY on Base!", url="https://b.com/y")
    c = Item(source="A", title="Other", url="https://a.com/x")
    assert [i.title for i in st.add_items([a, b, c])] == ["Ondo launches USDY on Base"]
    assert len(st.unused_items(48, 10)) == 1


RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>Centrifuge &amp; Schroders launch fund</title><link>https://n.com/1</link>
<description>&lt;p&gt;Tokenized EM debt fund on Solana&lt;/p&gt;</description>
<pubDate>%s</pubDate></item>
<item><title>Old story</title><link>https://n.com/2</link><pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate></item>
</channel></rss>"""


def test_rss_parse_filters_old_and_cleans_html():
    now = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT").encode()
    items = rss.parse_feed(RSS.replace(b"%s", now), {"name": "N", "tags": ["rwa"]}, 48)
    assert len(items) == 1
    assert items[0].title == "Centrifuge & Schroders launch fund"
    assert items[0].summary == "Tokenized EM debt fund on Solana"


def test_defillama_rwa_board_and_movers():
    protocols = [
        {"name": "BlackRock BUIDL", "slug": "blackrock-buidl", "category": "RWA", "tvl": 2e9,
         "change_7d": 20.0, "change_1d": 1.0, "chains": ["Ethereum"]},
        {"name": "Small", "slug": "small", "category": "RWA", "tvl": 1e6, "change_7d": 90.0},
        {"name": "Aave", "slug": "aave", "category": "Lending", "tvl": 3e10, "change_7d": 2.0},
    ]
    pools = [
        {"project": "blackrock-buidl", "symbol": "BUIDL", "chain": "Ethereum", "tvlUsd": 1e9, "apy": 3.9,
         "apyMean30d": 4.0, "apyPct7D": 1.5},
        {"project": "aave", "symbol": "USDC", "chain": "Ethereum", "tvlUsd": 1e9, "apy": 5.0},
    ]
    movers = defillama.tvl_movers(protocols, {"categories": ["RWA", "Lending"], "min_tvl_usd": 5e7,
                                              "tvl_move_pct_7d": 15})
    assert len(movers) == 1 and "BUIDL" in movers[0].title and "+20.0%" in movers[0].title
    board = defillama.rwa_yield_board(protocols, pools, {"min_tvl_usd": 1e7})
    assert len(board) == 1
    assert "TVL-weighted APY 3.90%" in board[0].title
    assert "+1.50pp" in board[0].summary


def test_load_archive(tmp_path):
    p = tmp_path / "tweets.js"
    p.write_text('window.YTD.tweets.part0 = [{"tweet": {"id_str": "1", "full_text": "RWA yields are compressing",'
                 ' "favorite_count": "12", "retweet_count": "3", "created_at": "x"}},'
                 ' {"tweet": {"id_str": "2", "full_text": "RT @a: hi", "favorite_count": "0", "retweet_count": "0"}}]')
    posts = load_archive(p)
    assert posts[0]["likes"] == 12 and posts[0]["type"] == "post"
    assert posts[1]["type"] == "repost"
