"""Shared source-family mappings for scoring and corpus ingestion."""

from src.models import SourceType


KIND_TO_SOURCE = {
    "official": SourceType.RSS,
    "media": SourceType.RSS,
    "analyst": SourceType.RSS,
    "datacenter": SourceType.RSS,
    "survey": SourceType.RSS,
    "card": SourceType.RSS,
    "license": SourceType.RSS,
    "security": SourceType.RSS,
    "supply": SourceType.RSS,
    "academic": SourceType.GOOGLE_NEWS,
    "venue": SourceType.GOOGLE_NEWS,
    "pricing": SourceType.OPENBB,
    "finance": SourceType.OPENBB,
    "jobs": SourceType.HACKERNEWS,
    "community": SourceType.HACKERNEWS,
    "gov": SourceType.GDELT,
    "gov_cn": SourceType.GDELT,
    "talent": SourceType.HACKERNEWS,
}

# `kind` is far too coarse for the community bucket: it holds Hacker News, npm,
# GitHub, 知乎, CSDN, 掘金, 虎扑, V2EX and more, so mapping the whole kind to
# HACKERNEWS labelled 3,371 CSDN answers and 6,477 知乎 rows as Hacker News.
# That is not a cosmetic label - `distinct_publishers` counts by
# (source_type, publisher), and `publisher_of` treats a platform host as a venue
# only within its own family. Wrong family, wrong independence maths.
#
# Anything not listed here falls through to KIND_TO_SOURCE, so this table only
# needs the sources whose family is not what their kind implies.
SOURCE_KEY_TO_SOURCE = {
    # --- platforms the corpus had been filing as Hacker News
    "zhihu_search": SourceType.V2EX,
    "zhihu_topic": SourceType.V2EX,
    "csdn_search": SourceType.V2EX,
    "csdn_ask": SourceType.V2EX,
    "csdn_hot": SourceType.V2EX,
    "juejin_search": SourceType.V2EX,
    "juejin_column": SourceType.V2EX,
    "juejin": SourceType.V2EX,
    "v2ex_search": SourceType.V2EX,
    "v2ex_action": SourceType.V2EX,
    "hupu_search": SourceType.REDDIT,
    "hupu": SourceType.REDDIT,
    "smzdm": SourceType.REDDIT,
    "douban": SourceType.REDDIT,
    "douban_explore": SourceType.REDDIT,
    "douban_rexxar": SourceType.REDDIT,
    "tieba_search": SourceType.V2EX,
    "toutiao_search": SourceType.GOOGLE_NEWS,
    "wechat_search": SourceType.RSS,
    "baidu_zhidao": SourceType.V2EX,
    "producthunt": SourceType.HACKERNEWS,
    # --- video and social platforms
    "bili_search": SourceType.BILIBILI,
    "bili_popular": SourceType.BILIBILI,
    "bili_popular_browser": SourceType.BILIBILI,
    "bili_rank": SourceType.BILIBILI,
    "youtube_cn": SourceType.YOUTUBE,
    "xhs_explore": SourceType.BILIBILI,
    "douyin_hot": SourceType.BILIBILI,
    "kuaishou_hot": SourceType.BILIBILI,
    "weibo_hot_api": SourceType.TWITTER,
    "weibo_hot": SourceType.TWITTER,
    "weibo_search": SourceType.TWITTER,
    # --- code and packages are their own family, not news
    "gh_search": SourceType.GITHUB,
    "github_issues": SourceType.GITHUB,
    "hf_model_search": SourceType.GITHUB,
    "hf_dataset_search": SourceType.GITHUB,
    "gh_llm": SourceType.GITHUB,
    "gh_agent": SourceType.GITHUB,
    "gh_mcp": SourceType.GITHUB,
    "gh_rag": SourceType.GITHUB,
    "gh_recent": SourceType.GITHUB,
    "gh_advisories": SourceType.GITHUB,
    "gh_npm": SourceType.GITHUB,
    "npm": SourceType.GITHUB,
    "npm_agent": SourceType.GITHUB,
    "pypi": SourceType.GITHUB,
    "hf_models_trend": SourceType.GITHUB,
    "hf_models_new": SourceType.GITHUB,
    "hf_datasets": SourceType.GITHUB,
    "hf_spaces": SourceType.GITHUB,
    "hf_licenses": SourceType.GITHUB,
    # --- news aggregators
    "googlenews_cn": SourceType.GOOGLE_NEWS,
    "googlenews_en": SourceType.GOOGLE_NEWS,
    "google_news": SourceType.GOOGLE_NEWS,
    "gdelt": SourceType.GDELT,
    # `topic.py` writes its platform rows under the full catalog key, so those
    # keys need an honest family here too -- otherwise every platform row from
    # a topic run is skipped as "unmapped kind: community".
    "weibo_search": SourceType.TWITTER,
    "bili_search": SourceType.BILIBILI,
    "zhihu_search": SourceType.V2EX,
    "zhihu_topic": SourceType.V2EX,
    "csdn_search": SourceType.V2EX,
    "csdn_ask": SourceType.V2EX,
    "juejin_search": SourceType.V2EX,
    "juejin_column": SourceType.V2EX,
    "v2ex_search": SourceType.V2EX,
    "hupu_search": SourceType.REDDIT,
    "toutiao_search": SourceType.GOOGLE_NEWS,
    "wechat_search": SourceType.RSS,
    "youtube_cn": SourceType.YOUTUBE,
    "dsapi": SourceType.GOOGLE_NEWS,
    # --- Q&A and forums on the English side
    "so_llm": SourceType.V2EX,
    "stackexchange_search": SourceType.V2EX,
    "se_ai": SourceType.V2EX,
    "devto": SourceType.RSS,
    "hn_ai": SourceType.HACKERNEWS,
    "hn_ai_monthly": SourceType.HACKERNEWS,
    "hn_comments": SourceType.HACKERNEWS,
    "hn_cn": SourceType.HACKERNEWS,
    "hn_front": SourceType.HACKERNEWS,
    "hn_hiring": SourceType.HACKERNEWS,
}

# Platform -> SourceType, for the UGC collector. The platform layer is where the
# "a stranger said it" signal lives, so each maps to the family that carries the
# right prior rather than all collapsing into one bucket.
PLATFORM_TO_SOURCE = {
    "douyin_hot": SourceType.BILIBILI,
    "kuaishou_hot": SourceType.BILIBILI,
    "bili_popular": SourceType.BILIBILI,
    "bili_popular_browser": SourceType.BILIBILI,
    "xhs_explore": SourceType.BILIBILI,
    "weibo_hot": SourceType.TWITTER,
    "threads": SourceType.TWITTER,
    "zhihu_hot": SourceType.V2EX,
    "tieba_python": SourceType.V2EX,
    "douban_explore": SourceType.REDDIT,
    "hupu": SourceType.REDDIT,
    "smzdm": SourceType.REDDIT,
    "producthunt": SourceType.HACKERNEWS,
    # The browser search collector uses the short platform keys.
    "zhihu": SourceType.V2EX,
    "bili": SourceType.BILIBILI,
    "xhs": SourceType.BILIBILI,
    # 抖音 and 快手 are short-video platforms with no dedicated SourceType.
    # They are filed under BILIBILI, the family whose credibility prior and
    # independence rule already model "video platform, creator-uploaded" -
    # `publisher_of` treats each platform host as its own venue, so 抖音 rows
    # count as one publisher named `douyin.com`, not as N independent voices.
    # Filing them anywhere else would need a new SourceType, which would change
    # the trust model for every existing row.
    "douyin": SourceType.BILIBILI,
    "kuaishou": SourceType.BILIBILI,
    "juejin": SourceType.V2EX,
    "csdn": SourceType.V2EX,
    "tieba": SourceType.V2EX,
    "weibo": SourceType.TWITTER,
    "douban": SourceType.REDDIT,
    "smzdm_search": SourceType.REDDIT,
    # The HTTP platform-search sources already name themselves `*_search`, so
    # they arrive through the http rows, not here - but keep the mapping in case
    # one is ever collected by the browser pass instead.
    "zhihu_search": SourceType.V2EX,
    "bili_search": SourceType.BILIBILI,
    "juejin_search": SourceType.V2EX,
    "csdn_search": SourceType.V2EX,
}
