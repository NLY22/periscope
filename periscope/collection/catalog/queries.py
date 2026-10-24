"""Shared query vocabularies for the source catalog."""

from datetime import datetime, timezone


AI_TERMS = (
    "AI agent", "LLM", "large language model", "open source model",
    "model release", "fine-tuning", "RAG", "retrieval augmented generation",
    "prompt injection", "AI safety", "alignment", "AI regulation",
    "GPU", "NVIDIA", "HBM", "AI chip", "inference", "quantization",
    "transformer", "diffusion model", "multimodal", "AI benchmark",
    "agent framework", "MCP", "model context protocol", "vLLM",
    "AI startup funding", "AI acquisition", "AI pricing", "token cost",
    "AI coding assistant", "Copilot", "Cursor", "Claude Code",
    "AI search", "vector database", "embedding", "AI dataset",
    "robotics AI", "AI video generation", "speech recognition",
)

CN_TERMS = (
    # Technical subjects.
    "大模型", "开源模型", "人工智能", "算力", "芯片", "英伟达",
    "AI 应用", "智能体", "模型发布", "融资",
    # Vendors, because the Chinese platforms argue about products by name.
    "DeepSeek", "通义千问", "豆包", "Kimi", "文心一言", "智谱", "Qwen",
    "OpenAI", "Claude", "MiniMax",
    # Question-shaped terms. What people say on a platform is phrased as a
    # question or a complaint far more often than as a product name, and those
    # threads are where "is this true" actually gets argued.
    "AI 好用吗", "AI 替代", "AI 失业", "大模型 幻觉", "AI 翻车",
    "本地部署", "提示词", "AI 副业",
)

# arXiv category + free-text pairs, so the academic leg is not one category deep.
ARXIV_QUERIES = (
    "cat:cs.AI", "cat:cs.CL", "cat:cs.LG", "cat:cs.CV", "cat:cs.SE",
    "cat:cs.IR", "cat:cs.DC", "cat:cs.CR",
    'all:"LLM agent"', 'all:"large language model"', 'all:"retrieval augmented"',
    'all:"prompt injection"', 'all:"model evaluation"', 'all:"AI safety"',
    'all:"multi-agent"', 'all:"in-context learning"',
)


def month_windows(months: int = 24) -> tuple:
    """Year-month labels for a backfill, newest first.

    Depth has two axes and paging only covers one. Paging walks deeper into the
    *same* result set, which for a relevance-sorted search means more of the same
    popular items. A date-filtered query reaches a different slice entirely: what
    was being said about a topic in March, not just what ranks highest today.

    Algolia and the Federal Register both accept a numeric date bound, so one
    source becomes N sources - one per month - without any new endpoint.
    """
    today = datetime.now(timezone.utc)
    out = []
    year, month = today.year, today.month
    for _ in range(months):
        out.append("%04d-%02d" % (year, month))
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return tuple(out)


RECENT_MONTHS = month_windows(12)
