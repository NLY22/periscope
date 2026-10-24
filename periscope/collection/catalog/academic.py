"""Academic source definitions."""

from ..models import Source
from .queries import AI_TERMS, CN_TERMS, ARXIV_QUERIES, RECENT_MONTHS


SOURCES = [
    Source("arxiv_ai", "arXiv cs.AI", "https://export.arxiv.org/api/query?search_query={query}&start={offset}&max_results={per_page}&sortBy=submittedDate&sortOrder=descending",
           "academic", fmt="rss", page_param="offset", page_start=0, page_step=100,
           per_page_param="per_page", per_page=100, max_pages=10, min_interval=3.0,
           paginate=True, queries=ARXIV_QUERIES, query_param="query"),
    Source("arxiv_cl", "arXiv cs.CL", "https://export.arxiv.org/api/query?search_query=cat:cs.CL&start={offset}&max_results={per_page}&sortBy=submittedDate&sortOrder=descending",
           "academic", fmt="rss", page_param="offset", page_start=0, page_step=100,
           per_page_param="per_page", per_page=100, max_pages=5, min_interval=3.0,
           paginate=True),
    Source("arxiv_lg", "arXiv cs.LG", "https://export.arxiv.org/api/query?search_query=cat:cs.LG&start={offset}&max_results={per_page}&sortBy=submittedDate&sortOrder=descending",
           "academic", fmt="rss", page_param="offset", page_start=0, page_step=100,
           per_page_param="per_page", per_page=100, max_pages=5, min_interval=3.0,
           paginate=True),
    Source("arxiv_agent", "arXiv: LLM agent", "https://export.arxiv.org/api/query?search_query=all:%22LLM+agent%22&start={offset}&max_results={per_page}&sortBy=submittedDate&sortOrder=descending",
           "academic", fmt="rss", page_param="offset", page_start=0, page_step=100,
           per_page_param="per_page", per_page=100, max_pages=5, min_interval=3.0,
           paginate=True),
    Source("openalex", "OpenAlex", "https://api.openalex.org/works?filter=title_and_abstract.search:{query}&per-page={per_page}&cursor={cursor}",
           "academic", items_path="results", title="title", url_field="doi",
           date_field="publication_date", cursor_path="meta.next_cursor",
           per_page_param="per_page", per_page=200, max_pages=50,
           queries=("large language model", "AI agent", "retrieval augmented generation",
                    "prompt injection", "model evaluation", "AI safety",
                    "multimodal model", "AI regulation"),
           query_param="query", min_interval=1.0),
]
