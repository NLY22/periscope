"""Official source definitions."""

from ..models import Source
from .queries import AI_TERMS, CN_TERMS, ARXIV_QUERIES, RECENT_MONTHS


SOURCES = [
    Source("openai", "OpenAI blog", "https://openai.com/news/rss.xml", "official", fmt="rss"),
    Source("google_ai", "Google AI blog", "https://blog.google/technology/ai/rss/", "official", fmt="rss"),
    Source("deepmind", "Google DeepMind", "https://deepmind.google/blog/rss.xml", "official", fmt="rss"),
    Source("msr", "Microsoft Research", "https://www.microsoft.com/en-us/research/feed/", "official", fmt="rss"),
    Source("nvidia", "NVIDIA blog", "https://blogs.nvidia.com/feed/", "official", fmt="rss"),
    Source("aws_ml", "AWS ML blog", "https://aws.amazon.com/blogs/machine-learning/feed/", "official", fmt="rss"),
    Source("cloudflare", "Cloudflare blog", "https://blog.cloudflare.com/rss/", "official", fmt="rss"),
    Source("github_blog", "GitHub blog", "https://github.blog/feed/", "official", fmt="rss"),
    Source("vercel", "Vercel blog", "https://vercel.com/atom", "official", fmt="rss"),
    Source("langchain_blog", "LangChain blog", "https://blog.langchain.dev/rss/", "official", fmt="rss"),
    Source("langchain_changelog", "LangChain changelog", "https://changelog.langchain.com/feed", "official", fmt="rss"),
    Source("openai_api_changelog", "OpenAI API changelog", "https://platform.openai.com/docs/changelog", "official", fmt="html", max_pages=1),
    Source("hf_blog", "HuggingFace blog", "https://huggingface.co/blog/feed.xml", "official", fmt="rss"),
    Source("databricks", "Databricks blog", "https://www.databricks.com/feed", "official", fmt="rss"),
    Source("snowflake_eng", "Snowflake engineering", "https://medium.com/feed/snowflake-engineering", "official", fmt="rss"),
]
