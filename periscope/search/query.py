"""Query enhancement: cleaning, entity extraction, cross-lingual term bridge, and multi-angle expansion."""
from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field


# 常见中文口语化填充词与助词
_CHINESE_FILLER = re.compile(
    r"^(请帮我|帮我|请问|请|查询|搜索|查找|分析|我想知道|我想了解|关于|有关|探讨|介绍一下|介绍)\s*"
    r"|请帮我|帮我|请问|请|分析|哪些|哪所|哪个|怎么样|如何|什么|有关|关于|的情况|的资料|的信息|最新情况|最新进展|一下|到底|究竟|详细说说|对比",
    re.IGNORECASE,
)

# 英文常见停用词与填充词
_ENGLISH_FILLER = re.compile(
    r"\b(please|help\s+me|find|search|about|what\s+is|how\s+to|tell\s+me\s+about|analyze|analysis\s+of|the|a|an|in|on|of|for|latest)\b",
    re.IGNORECASE,
)

_PUNCTUATION_PATTERN = re.compile(r"[，,。；;？?！!、“”\"'（）()【】\[\]：:/\\]+")
_YEAR_PATTERN = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")

# 英文专属检索源：对纯中文查询往往直接返回 0 条，必须提供英文术语分发
ENGLISH_SOURCES = {
    "arxiv_ai", "arxiv_cl", "arxiv_lg", "arxiv_agent", "openalex", "crossref_works",
    "stackexchange_search", "github_issues", "hn_ai", "hn_comments", "hn_ai_monthly",
    "pypi", "npm", "gh_search"
}

# 高频中英文前沿技术术语权威映射词典
_TERM_DICTIONARY: list[tuple[re.Pattern, str]] = [
    # AI / LLM / Agent
    (re.compile(r"人形机器人|仿人机器人", re.IGNORECASE), "humanoid robot"),
    (re.compile(r"具身智能|具身AI", re.IGNORECASE), "embodied AI"),
    (re.compile(r"大语言模型|大模型", re.IGNORECASE), "large language model"),
    (re.compile(r"智能体|智能代理", re.IGNORECASE), "AI agent"),
    (re.compile(r"灵巧手|机械手", re.IGNORECASE), "dexterous hand"),
    (re.compile(r"检索增强|知识库检索", re.IGNORECASE), "retrieval augmented generation"),
    (re.compile(r"强化学习", re.IGNORECASE), "reinforcement learning"),
    (re.compile(r"提示词工程|提示工程", re.IGNORECASE), "prompt engineering"),
    (re.compile(r"多模态", re.IGNORECASE), "multimodal"),
    (re.compile(r"扩散模型", re.IGNORECASE), "diffusion model"),
    (re.compile(r"微调", re.IGNORECASE), "fine-tuning"),
    (re.compile(r"量化", re.IGNORECASE), "quantization"),
    (re.compile(r"推理加速|模型推理", re.IGNORECASE), "inference optimization"),
    (re.compile(r"注意力机制", re.IGNORECASE), "attention mechanism"),
    (re.compile(r"端到端", re.IGNORECASE), "end-to-end"),
    (re.compile(r"自动驾驶", re.IGNORECASE), "autonomous driving"),
    (re.compile(r"长上下文", re.IGNORECASE), "long context"),
    (re.compile(r"模型对齐|安全对齐", re.IGNORECASE), "alignment"),
    (re.compile(r"评测|基准测试", re.IGNORECASE), "benchmark"),
    (re.compile(r"多智能体|多Agent", re.IGNORECASE), "multi-agent"),
    # Systems / PL
    (re.compile(r"异步运行时", re.IGNORECASE), "async runtime"),
    (re.compile(r"生命周期", re.IGNORECASE), "lifetime"),
    (re.compile(r"所有权", re.IGNORECASE), "ownership"),
    (re.compile(r"借用检查", re.IGNORECASE), "borrow checker"),
    (re.compile(r"内存安全", re.IGNORECASE), "memory safety"),
    (re.compile(r"垃圾回收", re.IGNORECASE), "garbage collection"),
    (re.compile(r"并发", re.IGNORECASE), "concurrency"),
    (re.compile(r"死锁", re.IGNORECASE), "deadlock"),
    (re.compile(r"分布式系统", re.IGNORECASE), "distributed systems"),
    (re.compile(r"性能优化", re.IGNORECASE), "performance optimization"),
    (re.compile(r"静态分析", re.IGNORECASE), "static analysis"),
    (re.compile(r"编译器", re.IGNORECASE), "compiler"),
    (re.compile(r"零拷贝", re.IGNORECASE), "zero-copy"),
    (re.compile(r"向量数据库", re.IGNORECASE), "vector database"),
    (re.compile(r"图神经网络", re.IGNORECASE), "graph neural network"),
    (re.compile(r"固态电池", re.IGNORECASE), "solid-state battery"),
    (re.compile(r"减速器|谐波减速", re.IGNORECASE), "harmonic drive reducer"),
    (re.compile(r"芯片|半导体", re.IGNORECASE), "semiconductor chip"),
    (re.compile(r"算力", re.IGNORECASE), "computing power GPU"),
]


@dataclass(frozen=True)
class EnhancedQuery:
    """结构化查询增强对象。"""

    raw_query: str
    clean_query: str
    english_query: str = ""
    core_terms: tuple[str, ...] = ()
    variants: tuple[str, ...] = ()
    years: tuple[str, ...] = ()

    @property
    def platform_query(self) -> str:
        """专用于垂直中文平台的紧凑查询词。"""
        if self.core_terms:
            return " ".join(self.core_terms[:3])
        return self.clean_query or self.raw_query

    def query_for_source(self, source_key: str) -> str:
        """根据来源语言属性智能分发查询词：英文专属源返回英文术语，中文平台返回核心中文词。"""
        if source_key in ENGLISH_SOURCES and self.english_query:
            return self.english_query
        return self.platform_query


def translate_to_en(raw_query: str) -> str:
    """将中文查询通过权威专业术语库智能映射为英文技术查询短语。"""
    raw = (raw_query or "").strip()
    if not raw:
        return ""

    matched_en_terms: list[str] = []
    # 提取英文已有的原生术语（如 Rust, Tokio, GIL, LLM, Python, GPU 等）
    ascii_terms = re.findall(r"[a-zA-Z0-9_+.-]{2,}", raw)
    years = _YEAR_PATTERN.findall(raw)

    text_to_match = raw
    for pattern, en_trans in _TERM_DICTIONARY:
        if pattern.search(text_to_match):
            if en_trans not in matched_en_terms:
                matched_en_terms.append(en_trans)

    parts: list[str] = []
    if matched_en_terms:
        parts.extend(matched_en_terms[:3])
    
    # 补充原查询中的专业英文单词（去重）
    for term in ascii_terms:
        term_clean = term.lower()
        if term not in years and not any(term_clean in m.lower() for m in matched_en_terms):
            parts.append(term)

    if years:
        parts.append(years[0])

    if parts:
        return " ".join(dict.fromkeys(parts))

    # 若没有任何匹配且包含英文单词，直接返回英文单词组合
    if ascii_terms:
        return " ".join(ascii_terms[:4])

    return ""


def clean_query(raw_query: str) -> str:
    """清洗口语化助词与无效标点，提取核心搜索关键词。"""
    raw = (raw_query or "").strip()
    if not raw:
        return ""

    # 1. 提取年份
    years = _YEAR_PATTERN.findall(raw)

    # 2. 剥离中文和英文填充助词
    cleaned = _CHINESE_FILLER.sub(" ", raw)
    cleaned = _ENGLISH_FILLER.sub(" ", cleaned)
    cleaned = _PUNCTUATION_PATTERN.sub(" ", cleaned)

    # 3. 提取分词/词元
    tokens = re.findall(r"[a-zA-Z0-9_+.-]{2,}|[\u4e00-\u9fff]{2,}", cleaned)

    # 4. 过滤极高频无区分度通用词
    stop_words = {"资料", "信息", "情况", "问题", "分析", "最新", "进展", "介绍", "大家", "讨论"}
    valid_tokens = [t for t in tokens if t not in stop_words and t not in set(years)]

    if not valid_tokens:
        parts = [p.strip() for p in _PUNCTUATION_PATTERN.split(raw) if p.strip()]
        return " ".join(parts[:3]) if parts else raw

    unique_years = list(dict.fromkeys(years))
    unique_tokens = list(dict.fromkeys(valid_tokens))

    result_terms = [*unique_years, *unique_tokens]
    return " ".join(result_terms[:6])


def extract_core_terms(raw_query: str) -> tuple[str, ...]:
    """提取核心检索词元元组。"""
    cleaned = clean_query(raw_query)
    tokens = re.findall(r"[a-zA-Z0-9_+.-]+|[\u4e00-\u9fff]+", cleaned)
    return tuple(dict.fromkeys(tokens))


def generate_search_variants(clean_topic: str, depth: int = 3, year: str = "") -> list[str]:
    """根据清洗后的核心主题，生成多视角搜索变体。"""
    topic = (clean_topic or "").strip()
    if not topic:
        return []

    if not year:
        year = str(_dt.datetime.now().year)

    ordered = [
        topic,
        f"{topic} {year}",
        f"{topic} 最新进展",
        f"{topic} 深度分析",
        f"{topic} site:news.cn",
        f"{topic} site:people.com.cn",
        f"{topic} site:zhihu.com",
        f"{topic} 技术报告",
    ]

    out = []
    seen = set()
    for q in ordered[:max(1, depth)]:
        q_norm = q.lower().strip()
        if q_norm not in seen:
            seen.add(q_norm)
            out.append(q)
    return out


def enhance_query(raw_query: str, depth: int = 3) -> EnhancedQuery:
    """统一查询增强入口。"""
    raw = (raw_query or "").strip()
    cleaned = clean_query(raw)
    terms = extract_core_terms(cleaned or raw)
    years = tuple(dict.fromkeys(_YEAR_PATTERN.findall(raw)))
    variants = tuple(generate_search_variants(cleaned or raw, depth=depth))
    en_query = translate_to_en(raw)

    return EnhancedQuery(
        raw_query=raw,
        clean_query=cleaned or raw,
        english_query=en_query,
        core_terms=terms,
        variants=variants,
        years=years,
    )
