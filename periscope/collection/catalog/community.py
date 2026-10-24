"""Community source definitions."""

from ..models import Source
from .queries import AI_TERMS, CN_TERMS, ARXIV_QUERIES, RECENT_MONTHS


SOURCES = [
    Source("gh_search", "GitHub: agent repos", "https://api.github.com/search/repositories?q={query}&sort=stars&per_page={per_page}&page={page}",
           "community", items_path="items", title="full_name", url_field="html_url",
           date_field="pushed_at", author_field="owner.login", text_field="description",
           page_param="page", per_page_param="per_page", per_page=100, max_pages=10,
           queries=("agent stars:>2000", "llm stars:>1000", "ai stars:>5000",
                    "rag stars:>1000", "mcp stars:>200", "transformer stars:>1000",
                    "openai stars:>1000", "inference stars:>500",
                    "vector-database stars:>500", "fine-tuning stars:>500"),
           query_param="query", min_interval=6.5),
    Source("gh_llm", "GitHub topic: llm", "https://api.github.com/search/repositories?q=topic:llm+stars:>1000&sort=updated&per_page={per_page}&page={page}",
           "community", items_path="items", title="full_name", url_field="html_url",
           date_field="pushed_at", author_field="owner.login", text_field="description",
           page_param="page", per_page_param="per_page", per_page=100, max_pages=5,
           min_interval=6.5),
    Source("gh_agent", "GitHub topic: ai-agent", "https://api.github.com/search/repositories?q=topic:ai-agent+stars:>500&sort=updated&per_page={per_page}&page={page}",
           "community", items_path="items", title="full_name", url_field="html_url",
           date_field="pushed_at", author_field="owner.login", text_field="description",
           page_param="page", per_page_param="per_page", per_page=100, max_pages=5,
           min_interval=6.5),
    Source("npm", "npm search (LLM terms)", "https://registry.npmjs.org/-/v1/search?text={query}&size={per_page}&from={offset}",
           "community", items_path="objects", title="package.name",
           url_field="package.links.npm", date_field="package.date",
           author_field="package.publisher.username", text_field="package.description",
           page_param="offset", page_start=0, page_step=100, per_page_param="per_page",
           per_page=100, max_pages=10,
           queries=("llm", "ai-agent", "openai", "rag", "mcp", "transformer",
                    "vector-database", "prompt", "embedding", "copilot"),
           query_param="query", min_interval=0.6),
    Source("pypi", "PyPI JSON", "https://pypi.org/pypi/langchain/json", "community",
           title="info.name", url_field="info.package_url", author_field="info.author",
           text_field="info.summary", max_pages=1),
    Source("npm_agent", "npm search: agent", "https://registry.npmjs.org/-/v1/search?text=ai-agent&size={per_page}&from={offset}",
           "community", items_path="objects", title="package.name",
           url_field="package.links.npm", date_field="package.date",
           author_field="package.publisher.username", text_field="package.description",
           page_param="offset", page_start=0, page_step=100, per_page_param="per_page",
           per_page=100, max_pages=2),
    Source("slashdot", "Slashdot", "https://rss.slashdot.org/Slashdot/slashdotMain", "community", fmt="rss"),
    Source("hn_front", "HN Front Page", "https://hnrss.org/frontpage", "community", fmt="rss"),
    Source("hellogithub", "HelloGitHub", "https://hellogithub.com/rss", "community", fmt="rss"),
    Source("juejin", "掘金", "https://juejin.cn/rss", "community", fmt="rss"),
    Source("zhihu_daily", "知乎日报", "https://news-at.zhihu.com/api/4/news/latest", "community",
           items_path="stories", title="title", url_field="url", date_field="", max_pages=1),
    Source("v2ex_latest", "V2EX 最新", "https://www.v2ex.com/index.xml", "community", fmt="rss"),
    Source("hf_models_trend", "HF models trending", "https://huggingface.co/api/models?sort=trendingScore&direction=-1&limit={per_page}",
           "community", title="modelId", url_field="modelId", date_field="createdAt",
           author_field="author", per_page_param="per_page", per_page=100, max_pages=1),
    Source("hf_models_new", "HF models newest", "https://huggingface.co/api/models?sort=createdAt&direction=-1&limit={per_page}",
           "community", title="modelId", url_field="modelId", date_field="createdAt",
           author_field="author", per_page_param="per_page", per_page=100, max_pages=1),
    Source("hf_datasets", "HF datasets", "https://huggingface.co/api/datasets?sort=trendingScore&direction=-1&limit={per_page}",
           "community", title="id", url_field="id", author_field="author",
           per_page_param="per_page", per_page=100, max_pages=1),
    Source("hf_spaces", "HF spaces", "https://huggingface.co/api/spaces?sort=trendingScore&direction=-1&limit={per_page}",
           "community", title="id", url_field="id", author_field="author",
           per_page_param="per_page", per_page=100, max_pages=1),
    Source("so_llm", "Stack Overflow (LLM)", "https://api.stackexchange.com/2.3/questions?order=desc&sort=votes&tagged=large-language-model&site=stackoverflow&pagesize=50&page={page}",
           "community", items_path="items", title="title", url_field="link",
           date_field="creation_date", author_field="owner.display_name",
           page_param="page", per_page_param="pagesize", per_page=50, max_pages=3),
    Source("se_ai", "Stack Exchange AI", "https://api.stackexchange.com/2.3/questions?order=desc&sort=votes&site=ai&pagesize=50&page={page}",
           "community", items_path="items", title="title", url_field="link",
           date_field="creation_date", author_field="owner.display_name",
           page_param="page", per_page_param="pagesize", per_page=50, max_pages=3),
    Source("devto", "Dev.to", "https://dev.to/api/articles?per_page={per_page}&page={page}",
           "community", title="title", url_field="url", date_field="published_at",
           author_field="user.name", text_field="description",
           page_param="page", per_page_param="per_page", per_page=100, max_pages=3),
    Source("hn_ai", "HN search (AI terms)",
           "https://hn.algolia.com/api/v1/search?query={query}&tags=story&hitsPerPage={per_page}&page={page}",
           "community", items_path="hits", title="title", url_field="url",
           date_field="created_at", author_field="author", text_field="story_text",
           page_param="page", page_start=0, per_page_param="hitsPerPage", per_page=100,
           max_pages=10, queries=AI_TERMS, query_param="query", min_interval=1.0),
    Source("hn_ai_monthly", "HN search (AI terms, by month)",
           "https://hn.algolia.com/api/v1/search?query={query}&tags=story"
           "&numericFilters=created_at_i>{after},created_at_i<{before}"
           "&hitsPerPage={per_page}&page={page}",
           "community", items_path="hits", title="title", url_field="url",
           date_field="created_at", author_field="author", text_field="story_text",
           page_param="page", page_start=0, per_page_param="hitsPerPage", per_page=100,
           max_pages=3, queries=AI_TERMS[:12], query_param="query",
           window_days=30, windows=6, min_interval=1.0),
    Source("hn_cn", "HN search (中文词)",
           "https://hn.algolia.com/api/v1/search?query={query}&tags=story&hitsPerPage={per_page}&page={page}",
           "community", items_path="hits", title="title", url_field="url",
           date_field="created_at", author_field="author", text_field="story_text",
           page_param="page", page_start=0, per_page_param="hitsPerPage", per_page=100,
           max_pages=5, queries=CN_TERMS, query_param="query", min_interval=1.0),
    Source("hn_comments", "HN comments (AI terms)",
           "https://hn.algolia.com/api/v1/search?query={query}&tags=comment&hitsPerPage={per_page}&page={page}",
           "community", items_path="hits", title="story_title", url_field="story_url",
           date_field="created_at", author_field="author", text_field="comment_text",
           page_param="page", page_start=0, per_page_param="hitsPerPage", per_page=100,
           max_pages=5, queries=AI_TERMS[:20], query_param="query", min_interval=1.0),
    Source("gh_recent", "GitHub: new this quarter", "https://api.github.com/search/repositories?q=created:>2026-07-01+stars:>300&sort=stars&per_page={per_page}&page={page}",
           "community", items_path="items", title="full_name", url_field="html_url",
           date_field="created_at", author_field="owner.login", text_field="description",
           page_param="page", per_page_param="per_page", per_page=100, max_pages=4,
           min_interval=2.5),
    Source("gh_mcp", "GitHub topic: mcp", "https://api.github.com/search/repositories?q=topic:model-context-protocol+stars:>100&sort=updated&per_page={per_page}&page={page}",
           "community", items_path="items", title="full_name", url_field="html_url",
           date_field="pushed_at", author_field="owner.login", text_field="description",
           page_param="page", per_page_param="per_page", per_page=100, max_pages=2,
           min_interval=2.5),
    Source("gh_rag", "GitHub topic: rag", "https://api.github.com/search/repositories?q=topic:rag+stars:>500&sort=updated&per_page={per_page}&page={page}",
           "community", items_path="items", title="full_name", url_field="html_url",
           date_field="pushed_at", author_field="owner.login", text_field="description",
           page_param="page", per_page_param="per_page", per_page=100, max_pages=2,
           min_interval=2.5),
    Source("bili_rank", "Bilibili ranking", "https://api.bilibili.com/x/web-interface/ranking/v2?rid=0&type=all",
           "community", items_path="data.list", title="title", url_field="short_link_v2",
           date_field="pubdate", author_field="owner.name", text_field="desc", max_pages=1),
    Source("zhihu_search", "知乎搜索",
           # Answers 400 without the session cookie, 200 with it. Measured with
           # the exported `z_c0`: offset 0/20/40/.../200 each returned 14-20
           # rows and kept yielding new titles all the way to offset 200, where
           # the run stopped at 153 unique results. The corpus previously held
           # 1,733 知乎 rows taken without the cookie, which is why the ceiling
           # looked like 14.
           "https://www.zhihu.com/api/v4/search_v3"
           "?t=general&q={query}&offset={offset}&limit=20"
           "&lc_idx={offset}&show_all_topics=0&correction=1",
           "community", items_path="data", title="object.title",
           # `general` mixes answers, articles and topics; a topic result has
           # no `object.title` but does have `highlight.title`, so a single
           # path dropped whole result types.
           title_fallback=("highlight.title", "title", "object.question.name"),
           url_field="object.url",
           url_fallback=("object.url", "url"),
           date_field="object.created_time",
           author_field="object.author.name", text_field="object.excerpt",
           page_param="offset", page_start=0, page_step=20,
           per_page=20, max_pages=12,
           queries=CN_TERMS, query_param="query",
           headers=(("Referer", "https://www.zhihu.com/search"),
                    ("x-requested-with", "fetch")),
           cookies_file="zhihu", min_interval=1.5),
    Source("bili_search", "B站搜索",
           # `/x/web-interface/search/type` answers 412 "request was banned" on
           # the first request and returns 200 only sporadically; the `wbi`
           # variant of the same endpoint answered 6/6 consecutive probes and
           # paged to 196 unique videos over 10 pages. The wbi prefix is not a
           # signature we forge - it is the endpoint the site's own front end
           # calls, and it accepts an unsigned request.
           #
           # `pubtime_begin_s` / `pubtime_end_s` are honoured, which makes this
           # the only Chinese platform so far with real time depth: a query
           # bounded to 2024-01 returned 535 matches, 2024-07 returned 911.
           # Without the bound every B站 row lands in the month it was crawled.
           "https://api.bilibili.com/x/web-interface/wbi/search/type"
           "?search_type=video&keyword={query}&page={page}"
           "&pubtime_begin_s={after}&pubtime_end_s={before}",
           "community", items_path="data.result", title="title",
           url_field="arcurl", date_field="pubdate",
           author_field="author", text_field="description",
           # The search response already carries the counters, so "collect the
           # signal" costs nothing extra here. Names are kept as B站 spells them.
           metrics=("play", "like", "favorites", "danmaku", "video_review=reply"),
           id_fields=("bvid", "aid", "mid"),
           page_param="page", page_start=1, page_step=1,
           per_page=20, max_pages=10,
           queries=CN_TERMS, query_param="query",
           # One window per year over 2024-2026, not per quarter. Measured:
           # five separate 90-day windows returned 20/20/20/20/20 unique titles,
           # so the window does reach different slices. But each window x term x
           # page is a request, and 28 terms x 12 quarters x 10 pages is 3,360
           # requests at 3 s spacing - three hours for one source. Four year
           # windows x 28 terms x 10 pages is 1,120, which a run can finish.
           window_days=365, windows=4,
           headers=(("Referer", "https://search.bilibili.com/"),),
           min_interval=3.0),
    Source("juejin_search", "掘金搜索",
           # GET answers 200 with results, so the earlier note that only POST
           # works was wrong - what actually failed was the `cursor`. The corpus
           # held 1,701 掘金 rows because the term list was `CN_TERMS[:1]` and
           # the page cap was 1: depth had never been asked for.
           "https://api.juejin.cn/search_api/v1/search"
           "?query={query}&id_type=0&cursor={cursor}&limit=20"
           "&search_type=1&sort_type=0",
           "community", items_path="data", title="result_model.article_info.title",
           url_field="result_model.article_info.article_id",
           date_field="result_model.article_info.ctime",
           author_field="result_model.author_user_info.user_name",
           text_field="result_model.article_info.brief_content",
           page_param="cursor", page_start=0, page_step=20,
           per_page=20, max_pages=5,
           queries=CN_TERMS, query_param="query",
           headers=(("Referer", "https://juejin.cn/"),),
           min_interval=1.5),
    Source("csdn_search", "CSDN 搜索",
           # `result_vos` moved from `data.result_vos` to the root object. The
           # old path still parses (it just yields nothing) so the source kept
           # reporting 0 without an error - measured today: 30 rows at the root,
           # None under `data`. Both blog and ask now share this envelope.
           "https://so.csdn.net/api/v3/search?q={query}&t=blog&p={page}"
           "&s=0&tm=0&lv=-1&ft=0&l=&u=&ct=-1&pnt=-1&ry=-1&ss=-1&dct=-1&vco=-1"
           "&cc=-1&sc=-1&akt=-1&art=-1&ca=-1&prs=&pre=&ecc=-1&ebc=-1&urw=&ia=1"
           "&dId=&cl=-1&scl=-1&tcl=-1&platform=pc",
           "community", items_path="result_vos", title="title",
           url_field="url", date_field="create_time",
           author_field="nickname", text_field="description",
           page_param="page", page_start=1, page_step=1,
           per_page=20, max_pages=15,
           queries=CN_TERMS, query_param="query",
           headers=(("Referer", "https://so.csdn.net/"),),
           min_interval=1.2),
    Source("toutiao_search", "头条搜索",
           "https://so.toutiao.com/search?keyword={query}&pd=synthesis",
           "community", fmt="html",
           html_pattern=r'"title"\s*:\s*"([^"]{10,120})"',
           queries=CN_TERMS[:14], query_param="query",
           max_pages=1,
           headers=(("Referer", "https://so.toutiao.com/"),),
           min_interval=2.5),
    Source("wechat_search", "微信公众号（搜狗）",
           "https://weixin.sogou.com/weixin?type=2&query={query}&ie=utf8",
           "community", fmt="html",
           html_pattern=r'<h3>\s*<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
           queries=CN_TERMS[:14], query_param="query",
           max_pages=1,
           headers=(("Referer", "https://weixin.sogou.com/"),),
           min_interval=3.0),
    Source("hupu_search", "虎扑搜索",
           "https://bbs.hupu.com/search?q={query}",
           "community", fmt="html",
           html_pattern=r'<a[^>]*href="([^"]*?/\d+\.html)"[^>]*>(.*?)</a>',
           queries=CN_TERMS, query_param="query",
           max_pages=1,
           headers=(("Referer", "https://bbs.hupu.com/"),),
           min_interval=2.0,
           # Measured 2026-10-19: the search page is client-rendered. A 121 KB
           # HTML response carries the query only in `<title>` and `<meta>`; the
           # thread list arrives over XHR, so the regex above matches nothing.
           # It also answers HTTP 405 intermittently (5 of 8 probes), which
           # `RATE_LIMIT_STATUS` reads as "slow down" and retries three times -
           # spending about 12s of a 30s budget on a source that cannot yield a
           # row either way. Declared, so `fanout` can skip it instead of paying
           # that cost on every run, and so a 0-row result is explained rather
           # than looking like an empty topic.
           needs_browser=True, profile="hupu"),
    Source("v2ex_search", "V2EX 搜索（sov2ex）",
           "https://www.sov2ex.com/api/search?q={query}&size=20&from={offset}"
           "&sort=created",
           "community", items_path="hits", title="_source.title",
           url_field="_source.id",
           date_field="_source.created", author_field="_source.member",
           text_field="_source.content",
           page_param="offset", page_start=0, page_step=20,
           per_page=20, max_pages=5,
           queries=CN_TERMS, query_param="query",
           headers=(("Referer", "https://www.sov2ex.com/"),),
           # sov2ex is a bag-of-words index: 「固态电池」 matches posts about 固态
           # drives and 电池 separately. Measured: 511 hits, none of the first
           # twenty mentioning the phrase; quoting the query returns 0. So the
           # filter is ours to apply.
           require_query_in_title=True,
           min_interval=1.5),
    Source("csdn_ask", "CSDN 问答",
           # The `t=ask` response puts its rows at the *root* `result_vos`, not
           # under `data` the way `t=blog` does - same API, different envelope.
           # Reading `data.result_vos` returned 0 for 28 requests.
           "https://so.csdn.net/api/v3/search?q={query}&t=ask&p={page}"
           "&s=0&tm=0&lv=-1&ft=0&l=&u=&ct=-1&pnt=-1&ry=-1&ss=-1&dct=-1&vco=-1"
           "&cc=-1&sc=-1&akt=-1&art=-1&ca=-1&prs=&pre=&ecc=-1&ebc=-1&urw=&ia=1"
           "&dId=&cl=-1&scl=-1&tcl=-1&platform=pc",
           "community", items_path="result_vos", title="title",
           url_field="url", date_field="create_time",
           author_field="nickname", text_field="description",
           page_param="page", page_start=1, page_step=1,
           per_page=20, max_pages=5,
           queries=CN_TERMS, query_param="query",
           headers=(("Referer", "https://so.csdn.net/"),),
           min_interval=1.5),
    Source("juejin_column", "掘金专栏",
           "https://api.juejin.cn/search_api/v1/search"
           "?query={query}&id_type=2&cursor={cursor}&limit=20",
           "community", items_path="data", title="result_model.article_info.title",
           url_field="result_model.article_info.article_id",
           date_field="result_model.article_info.ctime",
           author_field="result_model.author_user_info.user_name",
           text_field="result_model.article_info.brief_content",
           page_param="cursor", page_start=0, page_step=20,
           per_page=20, max_pages=5,
           queries=CN_TERMS, query_param="query",
           headers=(("Referer", "https://juejin.cn/"),),
           min_interval=1.5),
    Source("zhihu_topic", "知乎话题",
           "https://www.zhihu.com/api/v4/search_v3"
           "?t=topic&q={query}&offset={offset}&limit=20",
           "community", items_path="data", title="highlight.title",
           title_fallback=("title", "object.name"),
           url_field="url", url_fallback=("object.url",),
           text_field="object.excerpt",
           page_param="offset", page_start=0, page_step=20,
           per_page=20, max_pages=3,
           queries=CN_TERMS, query_param="query",
           headers=(("Referer", "https://www.zhihu.com/search"),
                    ("x-requested-with", "fetch")),
           cookies_file="zhihu", min_interval=1.5),
    Source("youtube_cn", "YouTube 搜索",
           "https://www.youtube.com/results?search_query={query}",
           "community", fmt="html",
           html_pattern=r'"title"\s*:\s*\{"runs"\s*:\s*\[\{"text"\s*:\s*"([^"]{8,110})"\}\]',
           queries=CN_TERMS, query_param="query",
           max_pages=1,
           headers=(("Accept-Language", "zh-CN,zh;q=0.9"),),
           min_interval=3.0),
    Source("weibo_hot_api", "微博热搜（API）",
           "https://weibo.com/ajax/side/hotSearch",
           "community", items_path="data.realtime", title="word",
           url_field="word_scheme", text_field="note",
           max_pages=1,
           headers=(("Referer", "https://weibo.com/"),), min_interval=1.0),
    Source("weibo_search", "微博搜索",
           "https://weibo.com/ajax/statuses/search?q={query}&page={page}",
           "community", items_path="statuses", title="text_raw",
           url_field="mblogid", url_fallback=("mid", "id"),
           date_field="created_at", author_field="user.screen_name",
           text_field="text_raw",
           # The search payload carries the three counters that define a 微博
           # post's reach; no second request needed.
           metrics=("reposts_count=reposts", "comments_count=comments",
                    "attitudes_count=likes"),
           id_fields=("id", "mid"),
           page_param="page", page_start=1, page_step=1,
           per_page=50, max_pages=10,
           queries=CN_TERMS, query_param="query",
           headers=(("Referer", "https://weibo.com/"),),
           cookies_file="weibo", min_interval=2.5),
    Source("douban_rexxar", "豆瓣（API）",
           "https://m.douban.com/rexxar/api/v2/subject_collection/"
           "movie_showing/items?start={offset}&count=20",
           "community", items_path="subject_collection_items", title="title",
           url_field="url", text_field="description",
           page_param="offset", page_start=0, page_step=20,
           per_page=20, max_pages=2,
           headers=(("Referer", "https://m.douban.com/"),), min_interval=1.0),
    Source("bili_popular", "B站热门（分页）",
           "https://api.bilibili.com/x/web-interface/popular?ps={per_page}&pn={page}",
           "community", items_path="data.list", title="title",
           url_field="short_link_v2", date_field="pubdate",
           author_field="owner.name", text_field="desc",
           page_param="page", per_page_param="ps", per_page=50, max_pages=15,
           min_interval=0.6),
    Source("csdn_hot", "CSDN 热榜",
           "https://blog.csdn.net/phoenix/web/blog/hot-rank?page={offset}&pageSize={per_page}",
           "community", items_path="data", title="articleTitle",
           url_field="articleDetailUrl", date_field="hotRankScore",
           author_field="nickName", text_field="description",
           page_param="offset", page_start=0, page_step=1,
           per_page_param="pageSize", per_page=50, max_pages=3,
           min_interval=0.6),
    Source("v2ex_api_latest", "V2EX 最新（API）",
           "https://www.v2ex.com/api/topics/latest.json",
           "community", title="title", url_field="url",
           date_field="last_touched", author_field="member.username",
           text_field="content", max_pages=1),
    Source("v2ex_api_hot", "V2EX 热门（API）",
           "https://www.v2ex.com/api/topics/hot.json",
           "community", title="title", url_field="url",
           date_field="last_touched", author_field="member.username",
           text_field="content", max_pages=1),
    Source("v2ex_tech_feed", "V2EX 技术", "https://www.v2ex.com/feed/tab/tech.xml",
           "community", fmt="rss"),
    Source("v2ex_qna_feed", "V2EX 问答", "https://www.v2ex.com/feed/tab/qna.xml",
           "community", fmt="rss"),
    Source("v2ex_ideas_feed", "V2EX 创意", "https://www.v2ex.com/feed/tab/ideas.xml",
           "community", fmt="rss"),
    Source("v2ex_apple_feed", "V2EX Apple", "https://www.v2ex.com/feed/tab/apple.xml",
           "community", fmt="rss"),
    Source("oschina_news2", "OSChina 资讯", "https://www.oschina.net/news/rss",
           "community", fmt="rss"),
]
