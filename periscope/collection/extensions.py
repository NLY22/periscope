"""Native search endpoints that complement generic web discovery.

Contracts: GitHub REST Search, Stack Exchange advanced search, Hugging Face
Hub API and Crossref REST /works. These are additions to the legacy catalog.
"""
from .models import Source


EXTRA_SOURCES = (
    Source("github_issues", "GitHub Issues 与 PR", "https://api.github.com/search/issues?q={query}&per_page={per_page}&page={page}",
           "community", items_path="items", title="title", url_field="html_url",
           date_field="created_at", author_field="user.login", text_field="body",
           queries=("",), query_param="query", page_param="page", per_page=30,
           id_fields=("number", "repository_url", "pull_request.url"),
           max_pages=3, min_interval=6.5, metrics=("comments",)),
    Source("stackexchange_search", "Stack Overflow 问题检索", "https://api.stackexchange.com/2.3/search/advanced?q={query}&site=stackoverflow&filter=withbody&pagesize={per_page}&page={page}",
           "community", items_path="items", title="title", url_field="link",
           date_field="creation_date", author_field="owner.display_name", text_field="body",
           queries=("",), query_param="query", page_param="page", per_page=30,
           max_pages=3, min_interval=1.0, metrics=("score", "answer_count", "view_count")),
    Source("hf_model_search", "Hugging Face 模型检索", "https://huggingface.co/api/models?search={query}&limit={per_page}&full=true",
           "community", title="id", url_field="id", date_field="lastModified",
           author_field="author", queries=("",), query_param="query", per_page=30,
           max_pages=1, metrics=("downloads", "likes")),
    Source("hf_dataset_search", "Hugging Face 数据集检索", "https://huggingface.co/api/datasets?search={query}&limit={per_page}&full=true",
           "community", title="id", url_field="id", date_field="lastModified",
           author_field="author", queries=("",), query_param="query", per_page=30,
           max_pages=1, metrics=("downloads", "likes")),
    Source("crossref_works", "Crossref 论文与出版元数据", "https://api.crossref.org/works?query.bibliographic={query}&rows={per_page}&offset={offset}",
           "academic", items_path="message.items", title="title.0", url_field="URL",
           author_field="author.0.family", text_field="abstract", queries=("",),
           query_param="query", page_param="offset", page_start=0, page_step=30,
           per_page=30, max_pages=3, min_interval=1.0),
)
