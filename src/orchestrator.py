"""Main orchestrator coordinating the entire workflow."""

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Literal, Optional
from urllib.parse import unquote_plus, urlsplit
import httpx
from rich.console import Console

from .console_icons import get_icons
from .models import Config, ContentItem, SOURCE_SPECS
from .corpus.sections import marker_sections_to_model
from .corpus.trust import thresholds_from
from .storage.manager import StorageManager, safe_output_path
from .services.email import EmailManager
from .services.webhook import WebhookNotifier
from .services.wechat import WeChatNotifier
from .scrapers.twitter import TwitterScraper
from .sources.registry import SCRAPER_BINDINGS, BuildContext, build_throttle, is_enabled
from .ai.client import create_ai_client
from .ai.analyzer import ContentAnalyzer
from .ai.summarizer import DailySummarizer
from .ai.enricher import ContentEnricher, EnrichmentBatchResult
from .ai.tokens import get_usage_snapshot
from .processing import ProfileRegistry
from .processing.tools import ToolRegistry


# Sentinel: distinguishes "AI client not yet built" from "built, but None".
_AI_UNSET = object()


_TRACKING_QUERY_PARAMETERS = {
    "_ga",
    "dclid",
    "fbclid",
    "gclid",
    "igshid",
    "li_fat_id",
    "mc_cid",
    "mc_eid",
    "msclkid",
    "ttclid",
    "twclid",
    "vero_id",
}


def _deduplication_url_key(url: str) -> tuple[str, str, str, str, Optional[int], str, str]:
    """Return a conservative URL identity key for cross-source deduplication."""
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    port = parsed.port
    if (scheme, port) in {("http", 80), ("https", 443)}:
        port = None

    path = parsed.path.rstrip("/") or "/"
    query_parts = []
    for part in parsed.query.split("&") if parsed.query else []:
        name = unquote_plus(part.partition("=")[0]).lower()
        if name.startswith("utm_") or name in _TRACKING_QUERY_PARAMETERS:
            continue
        query_parts.append(part)

    return (
        scheme,
        parsed.username or "",
        parsed.password or "",
        host,
        port,
        path,
        "&".join(query_parts),
    )


def _deduplication_item_key(item: ContentItem) -> tuple:
    """Identity key for cross-source deduplication.

    URL locators go through the existing seven-field normalisation so that
    tracking parameters and default ports keep collapsing as before. A
    non-URL locator (an app-only id, a note id) has no host or query to
    normalise, so it is compared verbatim under a distinct tag — a shape
    that can never collide with a URL key.
    """
    locator = item.locator or (str(item.url) if item.url else "")
    if "://" in locator:
        return _deduplication_url_key(locator)
    return ("locator", locator)


@dataclass
class BalancedDigestResult:
    """Items and selection statistics from balanced digest filtering."""

    items: List[ContentItem]
    enabled: bool = False
    group_counts: Dict[str, int] = field(default_factory=dict)
    group_limits: Dict[str, Optional[int]] = field(default_factory=dict)
    duplicate_categories: List[str] = field(default_factory=list)


@dataclass
class FilteringPipelineResult:
    """Items and statistics from score, topic, and digest filtering."""

    items: List[ContentItem]
    threshold_count: int
    topic_dedup_count: int
    topic_dedup_removed: int
    balanced_digest: BalancedDigestResult
    eligible_count: Optional[int] = None


@dataclass
class SourceFetchOutcome:
    """Result of fetching one configured source."""

    source_name: str
    status: Literal["success", "empty", "failure"]
    items: List[ContentItem] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, object]:
        result: Dict[str, object] = {
            "source": self.source_name,
            "status": self.status,
            "item_count": len(self.items),
        }
        if self.error is not None:
            result["error"] = self.error
        return result


@dataclass
class FetchReport:
    """Aggregate diagnostics for one fetch across configured sources."""

    outcomes: List[SourceFetchOutcome] = field(default_factory=list)

    @property
    def status(self) -> Literal["not_attempted", "success", "partial_failure", "failure"]:
        if not self.outcomes:
            return "not_attempted"
        if self.failed_count == len(self.outcomes):
            return "failure"
        if self.failed_count:
            return "partial_failure"
        return "success"

    @property
    def failed_count(self) -> int:
        return sum(outcome.status == "failure" for outcome in self.outcomes)

    @property
    def all_failed(self) -> bool:
        return bool(self.outcomes) and self.failed_count == len(self.outcomes)

    def failure_message(self) -> str:
        failures = "; ".join(
            f"{outcome.source_name}: {outcome.error or 'unknown error'}"
            for outcome in self.outcomes
            if outcome.status == "failure"
        )
        return f"All {len(self.outcomes)} attempted sources failed ({failures})"

    def to_dict(self) -> Dict[str, object]:
        return {
            "status": self.status,
            "attempted": len(self.outcomes),
            "successful": len(self.outcomes) - self.failed_count,
            "empty": sum(outcome.status == "empty" for outcome in self.outcomes),
            "failed": self.failed_count,
            "item_count": sum(len(outcome.items) for outcome in self.outcomes),
            "sources": [outcome.to_dict() for outcome in self.outcomes],
        }


class HorizonOrchestrator:
    """Orchestrates the complete workflow for content aggregation and analysis."""

    icons = get_icons()

    #: Ids stored by the most recent persist_to_corpus. Class-level so that an
    #: orchestrator built via __new__ — which several tests do — never hits an
    #: AttributeError before the first run has happened.
    last_new_item_ids: frozenset = frozenset()

    def __init__(
        self,
        config: Config,
        storage: StorageManager,
        console: Optional[Console] = None,
        profiles: Optional[ProfileRegistry] = None,
    ):
        """Initialize orchestrator.

        Args:
            config: Application configuration
            storage: Storage manager
            console: Shared Rich Console instance
        """
        self.config = config
        self.storage = storage
        self.console = console or Console(stderr=True)
        self.icons = get_icons(config.display.icon_style)
        self.profiles = profiles or ProfileRegistry.load(
            Path(config.processing.profiles_dir), config.processing.default_profile
        )
        self.profiles.validate_source_references(
            config.sources.model_dump(mode="json")
        )
        for profile_id in config.processing.profile_settings:
            self.profiles.get(profile_id)
        if config.digest.profile_order:
            configured_profiles = set(config.digest.profile_order)
            unknown_profiles = configured_profiles - self.profiles.ids
            if unknown_profiles:
                raise ValueError(
                    "digest.profile_order contains unknown profiles "
                    f"({', '.join(sorted(unknown_profiles))})"
                )
            config.digest.profile_order.extend(
                profile.id
                for profile in self.profiles.profiles
                if profile.id not in configured_profiles
            )
        self.email_manager = EmailManager(config.email, console=self.console) if config.email else None
        self.webhook_notifier = (
            WebhookNotifier(config.webhook, console=self.console, icons=self.icons)
            if config.webhook and config.webhook.enabled
            else None
        )
        self.wechat_notifier = (
            WeChatNotifier(config.wechat, self.storage, console=self.console, icons=self.icons)
            if config.wechat and config.wechat.enabled
            else None
        )
        self.last_fetch_report: Optional[FetchReport] = None
        self._corpus = None  # lazy: opened on first use
        self._retriever = None  # lazy: hybrid evidence retrieval policy when config.corpus.enabled
        self._ai_client_cache = _AI_UNSET  # lazy, optional (see _get_optional_ai_client)
        self._llm_cache = None  # lazy: shared persistent LLM response cache

    # ------------------------------------------------------------------- ai
    def _get_llm_cache(self):
        """Lazily open the persistent LLM response cache (data_dir/llm_cache.db).

        A free, rate-limited tier makes this load-bearing, not an
        optimization: crash-recovery runs and follow-up turns re-ask nearly
        identical prompts, and every one of them should cost zero calls.
        """
        if self._llm_cache is None:
            from .ai.cache import ResponseCache

            self._llm_cache = ResponseCache(Path(self.storage.data_dir) / "llm_cache.db")
        return self._llm_cache

    def _make_ai_client(self):
        """Create an AI client wrapped in the shared response cache + throttle.

        All LLM traffic goes through here so cache hits never touch the
        network and genuine misses are spaced by ai.throttle_sec.
        """
        from .ai.cache import CachingAIClient

        inner = create_ai_client(self.config.ai)
        return CachingAIClient(inner, self._get_llm_cache(), throttle_sec=self.config.ai.throttle_sec)

    # ------------------------------------------------------------------ corpus
    def _get_corpus(self):
        """Lazily open (or create) the persistent evidence corpus."""
        if self._corpus is None and self.config.corpus.enabled:
            from .corpus import Corpus

            path = Path(self.storage.data_dir) / self.config.corpus.path
            self._corpus = Corpus(path)
            self.console.print(
                f"{self.icons['fetched']} Evidence corpus: {path} "
                f"({self._corpus.stats()['items']} items)\n"
            )
        return self._corpus

    def persist_to_corpus(
        self, items: List[ContentItem], since: datetime
    ) -> frozenset:
        """Store fetched items in the evidence corpus (best-effort).

        Corpus failures must never break the daily pipeline, so errors are
        reported and swallowed; the run simply behaves like stateless Horizon.

        Returns the ids that were newly stored. `_get_corpus` caches its
        instance, so this must not close the corpus.
        """
        self.last_new_item_ids = frozenset()
        try:
            corpus = self._get_corpus()
            if corpus is None:
                return frozenset()
            candidate_ids = [item.id for item in items]
            already = corpus.known_ids(candidate_ids)
            new_ids = frozenset(i for i in candidate_ids if i not in already)
            run_id = corpus.begin_run(since)
            new_count = corpus.add_items(items, run_id)
            corpus.recompute_clusters(
                max_distance=self.config.corpus.cluster_max_distance,
                lookback_rows=self.config.corpus.cluster_lookback_rows,
            )
            corpus.finish_run(
                run_id,
                items_new=new_count,
                items_total_seen=len(items),
            )
            self.last_new_item_ids = new_ids
            self.console.print(
                f"{self.icons['fetched']} Corpus: +{new_count} new items "
                f"({corpus.stats()['items']} total)\n"
            )
            return new_ids
        except Exception as exc:
            self.console.print(
                f"[yellow]Corpus persistence failed (pipeline continues): {exc}[/yellow]\n"
            )
            return frozenset()

    # ------------------------------------------------------------- analysis
    def _get_claim_analyzer(self):
        """Build the claim analyzer over an open corpus; None if unavailable."""
        corpus = self._get_corpus()
        if corpus is None or not self.config.analysis.enabled:
            return None
        from .analysis import ClaimAnalyzer, ClaimStore

        return ClaimAnalyzer(
            store=ClaimStore(corpus),
            corpus=corpus,
            client=self._get_optional_ai_client(),
            max_claims_per_item=self.config.analysis.max_claims_per_item,
            evidence_per_claim=self.config.analysis.evidence_per_claim,
            grade_min_sources=self.config.analysis.grade_min_sources,
            content_chars=self.config.analysis.item_content_chars,
            claimable_only=self.config.analysis.claimable_only,
            triage_min_trust=self.config.analysis.triage_min_trust,
            thresholds=thresholds_from(self.config.analysis),
        )

    def _get_optional_ai_client(self):
        """AI client for background enrichment — optional by design.

        The daily digest needs a working LLM; claim analysis should degrade
        to deterministic mode (link + count) when no API key is configured.
        """
        if self._ai_client_cache is _AI_UNSET:
            try:
                self._ai_client_cache = self._make_ai_client()
            except Exception as exc:
                self.console.print(
                    f"[yellow]Analysis LLM unavailable ({type(exc).__name__}); "
                    f"running deterministic claim linking only.[/yellow]"
                )
                self._ai_client_cache = None
        return self._ai_client_cache

    # ------------------------------------------------------------- research
    def _get_retriever(self):
        """Build the hybrid evidence retriever for this run.

        Legs are attached only when they are both configured and possible:
        no corpus -> None (research falls back to its lexical core), no
        embedding model -> no vector leg, no API key -> no expansion. Nothing
        here may make a run fail that would otherwise work.
        """
        if self._retriever is not None:
            return self._retriever
        corpus = self._get_corpus()
        if corpus is None:
            return None

        from .ai.embeddings import EmbeddingClient
        from .corpus.retrieval import HybridRetriever
        from .corpus.semantic import EmbeddingIndex

        retrieval = self.config.retrieval
        tier = "claimable" if self.config.research.claimable_only else "all"

        index = None
        embedder = None
        if retrieval.semantic and retrieval.embedding_model:
            try:
                index = EmbeddingIndex(corpus, retrieval.embedding_model)
                embedder = EmbeddingClient.from_config(self.config)
                if embedder is None:
                    index = None
            except Exception as exc:
                self.console.print(
                    f"[yellow]Semantic retrieval disabled ({type(exc).__name__}).[/yellow]"
                )
                index = None
                embedder = None

        client = self._get_optional_ai_client() if retrieval.query_expansion else None

        self._retriever = HybridRetriever(
            corpus,
            tier=tier,
            index=index,
            embedder=embedder,
            expansion_client=client,
            expansion_max_terms=retrieval.expansion_max_terms,
            semantic_top_k=retrieval.semantic_top_k,
        )
        return self._retriever

    def get_claim_store(self):
        """Claim store over this run's corpus; None when corpus is disabled.

        Read side for the MCP/panel tool surface — analysis writes go through
        analyze_claims(), but callers only want to query what's there.
        """
        corpus = self._get_corpus()
        if corpus is None:
            return None
        from .analysis import ClaimStore

        return ClaimStore(corpus)

    def get_corpus(self):
        """Public accessor for the evidence corpus (None when disabled)."""
        return self._get_corpus()

    async def _collect_on_demand(self, question: str) -> int:
        """Fetch once from the key-less search sources for one sub-question.

        This is the widening move the corpus cannot do by itself: when nothing
        stored answers a sub-question, searching the open web for its terms and
        appending the result to the corpus turns "no evidence" into a first
        round of evidence. It only runs when explicitly enabled and only for a
        search source the user already configured, and it never touches the
        digest/notification path — the items just land in the corpus.
        """
        if not self.config.retrieval.on_demand_collection:
            return 0
        corpus = self._get_corpus()
        if corpus is None:
            return 0

        from copy import deepcopy
        from datetime import timedelta

        import httpx

        from .scrapers.gdelt import GDELTScraper
        from .scrapers.google_news import GoogleNewsScraper

        query = " ".join(str(question or "").split())[:120]
        if not query:
            return 0

        since = datetime.now(timezone.utc) - timedelta(days=30)
        built = []
        for name, source, scraper_cls in (
            ("GDELT", self.config.sources.gdelt, GDELTScraper),
            ("Google News", self.config.sources.google_news, GoogleNewsScraper),
        ):
            if source is None:
                continue
            probe = deepcopy(source)
            probe.query = query
            probe.enabled = True
            built.append((name, scraper_cls(probe, None), since))

        if not built:
            return 0

        items: List[ContentItem] = []
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            for name, scraper, window in built:
                try:
                    scraper.client = client
                    fetched = await scraper.fetch(window)
                    items.extend(fetched)
                except Exception as exc:
                    self.console.print(
                        f"[yellow]  on-demand {name} fetch failed ({type(exc).__name__})[/yellow]"
                    )

        if not items:
            return 0
        new = corpus.add_items(items)
        if new:
            corpus.recompute_clusters(
                max_distance=self.config.corpus.cluster_max_distance,
                lookback_rows=self.config.corpus.cluster_lookback_rows,
            )
        return new

    def get_research_session(self):
        """Assemble the long-session research loop over this run's corpus.

        Shared by the MCP server and the future web panel; the planner is
        the optional-LLM client, so research degrades to deterministic
        evidence gathering when no API key is set.
        """
        if not self.config.research.enabled:
            return None
        corpus = self._get_corpus()
        if corpus is None:
            return None
        from .research import LLMPlanner, ResearchSession, ResearchStore

        client = self._get_optional_ai_client()
        return ResearchSession(
            store=ResearchStore(corpus),
            corpus=corpus,
            planner=LLMPlanner(client) if client else None,
            evidence_per_question=self.config.research.evidence_per_question,
            max_evidence_chars=self.config.research.max_evidence_chars,
            planner_budget_per_invocation=self.config.research.planner_budget_per_invocation,
            claimable_only=self.config.research.claimable_only,
            retriever=self._get_retriever(),
            max_retrieval_rounds=self.config.research.max_retrieval_rounds,
            min_evidence_for_answer=self.config.research.min_evidence_for_answer,
            collector=self._collect_on_demand,
            report_template=self.config.research.report_template,
        )

    async def analyze_claims(self, items: List[ContentItem]) -> None:
        """Run the correctness loop over freshly fetched items (best-effort).

        Budgets: extraction touches only the top N items that are new to the
        corpus; grading gets its own call budget and only ever sees claims with
        enough independent sources. Unfinished claims persist and resume next
        run — so linking and grading still run when nothing is new.
        """
        try:
            analyzer = self._get_claim_analyzer()
            if analyzer is None:
                return
            # Only extraction costs LLM budget, so only extraction is limited
            # to items the corpus has not seen. A source with no publish time
            # re-delivers the same ids every run.
            fresh = [i for i in items if i.id in self.last_new_item_ids]
            ranked = sorted(
                fresh,
                key=lambda i: (i.processing.analysis.score or 0.0) if i.processing and i.processing.analysis else 0.0,
                reverse=True,
            )
            targets = ranked[: self.config.analysis.extract_top_items]
            extracted = 0
            for item in targets:
                extracted += len(await analyzer.extract_claims(item))
            linked = analyzer.link_all_pending()
            graded = await analyzer.grade_pending(
                max_calls=self.config.analysis.grade_budget_per_run
            )
            store_stats = analyzer.store.stats()
            self.console.print(
                f"{self.icons['ai']} Claims: {extracted} extracted, "
                f"{linked} linked, {graded} graded "
                f"(corpus total {store_stats['claims']}, "
                f"{store_stats['multi_source']} multi-source, "
                f"LLM calls {analyzer.llm_calls})\n"
            )
        except Exception as exc:
            self.console.print(
                f"[yellow]Claim analysis failed (pipeline continues): {exc}[/yellow]\n"
            )

    async def run(self, force_hours: int = None) -> None:
        """Execute the complete workflow.

        Args:
            force_hours: Optional override for time window in hours
        """
        self.console.print(
            f"[bold cyan]{self.icons['start']} Horizon - Starting aggregation...[/bold cyan]\n"
        )

        # Check email subscriptions if configured
        if (
            self.email_manager
            and self.config.email
            and self.config.email.enabled
            and self.config.email.imap_enabled
        ):
            self.console.print(f"{self.icons['email']} Checking for new email subscriptions...")
            self.email_manager.check_subscriptions(self.storage)

        try:
            # 1. Determine time window
            since = self._determine_time_window(force_hours)
            self.console.print(
                f"{self.icons['date']} Fetching content since: "
                f"{since.strftime('%Y-%m-%d %H:%M:%S')}\n"
            )

            # 2. Fetch content from all sources
            all_items = await self.fetch_all_sources(since)
            self.console.print(
                f"{self.icons['fetched']} Fetched {len(all_items)} items from all sources\n"
            )

            if self.last_fetch_report and self.last_fetch_report.all_failed:
                raise RuntimeError(self.last_fetch_report.failure_message())

            if not all_items:
                self.console.print("[yellow]No new content found. Exiting.[/yellow]")
                return

            # 3. Merge cross-source duplicates (same URL from different sources)
            merged_items = self.merge_cross_source_duplicates(all_items)
            if len(merged_items) < len(all_items):
                self.console.print(
                    f"{self.icons['merge']} Merged "
                    f"{len(all_items) - len(merged_items)} cross-source duplicates "
                    f"→ {len(merged_items)} unique items\n"
                )

            # 4. Analyze with AI
            analyzed_items = await self.analyze_items(merged_items)
            self.console.print(
                f"{self.icons['ai']} Analyzed {len(analyzed_items)} items with AI\n"
            )

            # 5. Filter, deduplicate, and balance the digest
            filtering_result = await self.select_digest_items(
                analyzed_items,
            )
            important_items = filtering_result.items

            # Show per-sub-source selection breakdown
            selected_counts: Dict[str, int] = defaultdict(int)
            for item in important_items:
                key = f"{item.source_type.value}/{self._sub_source_label(item)}"
                selected_counts[key] += 1
            for source_key, count in sorted(selected_counts.items()):
                self.console.print(f"      {self.icons['detail']} {source_key}: {count}")
            self.console.print("")

            # 6. Search related stories + enrich with background knowledge (2nd AI pass)
            await self.enrich_items(important_items)

            # 6b. Correctness loop (Periscope): claims -> evidence -> verdicts.
            # Runs after scoring so extraction targets the highest-value items.
            await self.analyze_claims(analyzed_items)

            # 7. Generate and save daily summaries for each configured language
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            for lang in self.config.ai.languages:
                summarizer = DailySummarizer(
                    profile_names=self.profiles.names,
                    profile_order=self.config.digest.profile_order,
                )
                summary = await summarizer.generate_summary(important_items, today, len(all_items), language=lang)

                # Save to data/summaries/
                summary_path = self.storage.save_daily_summary(today, summary, language=lang)
                self.console.print(
                    f"{self.icons['save']} Saved {lang.upper()} summary to: {summary_path}\n"
                )

                # Copy to docs/ for GitHub Pages
                try:
                    from pathlib import Path

                    post_filename = f"{today}-summary-{lang}.md"
                    posts_dir = Path("docs/_posts")
                    posts_dir.mkdir(parents=True, exist_ok=True)

                    dest_path = safe_output_path(posts_dir, post_filename)

                    # Add Jekyll front matter
                    front_matter = (
                        "---\n"
                        "layout: default\n"
                        f"title: \"Horizon Summary: {today} ({lang.upper()})\"\n"
                        f"date: {today}\n"
                        f"lang: {lang}\n"
                        "---\n\n"
                    )

                    # Strip leading H1 header to avoid duplication with Jekyll title
                    summary_content = summary
                    first_line = summary_content.strip().split("\n")[0]
                    if first_line.startswith("# "):
                        parts = summary_content.split("\n", 1)
                        if len(parts) > 1:
                            summary_content = parts[1].strip()

                    with open(dest_path, "w", encoding="utf-8") as f:
                        f.write(front_matter + summary_content)

                    self.console.print(
                        f"{self.icons['document']} Copied {lang.upper()} summary "
                        f"to GitHub Pages: {dest_path}\n"
                    )
                except Exception as e:
                    self.console.print(
                        f"[yellow]{self.icons['warning']} Failed to copy "
                        f"{lang.upper()} summary to docs/: {e}[/yellow]\n"
                    )

                # Send email if configured
                if self.email_manager and self.config.email and self.config.email.enabled:
                    self.console.print(
                        f"{self.icons['email']} Sending {lang.upper()} email summary..."
                    )
                    subscribers = self.storage.load_subscribers()
                    subject = f"Horizon Summary ({lang.upper()}) - {today}"
                    self.email_manager.send_daily_summary(summary, subject, subscribers)

                # Send webhook notification if configured
                if self.webhook_notifier:
                    await self.webhook_notifier.send_daily_summary(
                        summary=summary,
                        important_items=important_items,
                        all_items_count=len(all_items),
                        date=today,
                        lang=lang,
                        summarizer=summarizer,
                    )
                if self.wechat_notifier:
                    await self.wechat_notifier.send_daily_summary(summary, lang)

            self.console.print(
                f"[bold green]{self.icons['success']} "
                "Horizon completed successfully![/bold green]"
            )
            usage = get_usage_snapshot()
            if usage.total_tokens > 0:
                self.console.print(
                    f"\n{self.icons['tokens']} Token usage this run: "
                    f"{usage.total_tokens} tokens "
                    f"(input: {usage.total_input_tokens}, output: {usage.total_output_tokens})"
                )
                for provider, u in sorted(usage.per_provider.items()):
                    if u.total <= 0:
                        continue
                    self.console.print(
                        f"   {self.icons['detail']} {provider}: {u.total} tokens "
                        f"(in: {u.input_tokens}, out: {u.output_tokens})"
                    )

        except Exception as e:
            self.console.print(
                f"[bold red]{self.icons['error']} Error: {e}[/bold red]"
            )

            # Send webhook failure notification if configured
            if self.webhook_notifier:
                await self.webhook_notifier.send_failure(
                    date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    error_message=str(e),
                )
            if self.wechat_notifier:
                await self.wechat_notifier.send_failure(
                    date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                    error_message=str(e),
                )

            raise

    def _determine_time_window(self, force_hours: int = None) -> datetime:
        if force_hours:
            since = datetime.now(timezone.utc) - timedelta(hours=force_hours)
        else:
            hours = self.config.collection.time_window_hours
            since = datetime.now(timezone.utc) - timedelta(hours=hours)
        return since

    async def fetch_all_sources(self, since: datetime) -> List[ContentItem]:
        """Fetch content from all configured sources.

        This is a stable stage entry point for integrations such as MCP.

        Args:
            since: Fetch items published after this time

        Returns:
            List[ContentItem]: All fetched items
        """
        self.last_fetch_report = None
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            ctx = BuildContext(
                extractors=self.config.extractors,
                throttle=build_throttle(),
            )
            tasks = []
            for spec in SOURCE_SPECS:
                source_config = getattr(self.config.sources, spec.config_field, None)
                if not is_enabled(source_config, spec):
                    continue
                scraper = SCRAPER_BINDINGS[spec.key](spec, source_config, client, ctx)
                if scraper is None:
                    continue
                tasks.append(self._fetch_with_progress(spec.label, scraper, since))

            # Fetch all concurrently
            outcomes = await asyncio.gather(*tasks)
            self.last_fetch_report = FetchReport(outcomes=list(outcomes))

            # Flatten successful and empty outcomes; failures remain in the report.
            all_items: List[ContentItem] = []
            for outcome in outcomes:
                all_items.extend(outcome.items)

            # Periscope: accumulate evidence in the persistent corpus.
            self.persist_to_corpus(all_items, since)

            return all_items

    async def _fetch_with_progress(
        self, name: str, scraper, since: datetime
    ) -> SourceFetchOutcome:
        """Fetch from a scraper with progress indication.

        Args:
            name: Source name for display
            scraper: Scraper instance
            since: Fetch items after this time

        Returns:
            SourceFetchOutcome: Named fetch result and diagnostics
        """
        self.console.print(f"{self.icons['fetch']} Fetching from {name}...")
        try:
            items = await scraper.fetch(since)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self.console.print(f"[red]   Failed to fetch {name}: {error}[/red]")
            return SourceFetchOutcome(
                source_name=name,
                status="failure",
                error=error,
            )

        self.console.print(f"   Found {len(items)} items from {name}")

        # Show per-sub-source breakdown when there are multiple sub-sources
        sub_counts: Dict[str, int] = defaultdict(int)
        for item in items:
            sub_counts[self._sub_source_label(item)] += 1
        if len(sub_counts) > 1:
            for sub, count in sorted(sub_counts.items()):
                self.console.print(f"      {self.icons['detail']} {sub}: {count}")

        return SourceFetchOutcome(
            source_name=name,
            status="success" if items else "empty",
            items=items,
        )

    @staticmethod
    def _sub_source_label(item: ContentItem) -> str:
        """Return a human-readable sub-source label for an item."""
        meta = item.metadata
        if meta.get("subreddit"):
            return f"r/{meta['subreddit']}"
        if meta.get("feed_name"):
            return meta["feed_name"]
        if meta.get("channel"):
            return f"@{meta['channel']}"
        if meta.get("period") and meta.get("repo"):
            return f"ossinsight:{meta.get('primary_language', 'all')}"
        if meta.get("repo"):
            return meta["repo"]
        if meta.get("watchlist"):
            return meta["watchlist"]
        if meta.get("source_name"):
            return meta["source_name"]
        if meta.get("gn_query"):
            return f"google_news:{meta['gn_query']}"
        if meta.get("domain"):
            return meta["domain"]
        return item.author or "unknown"

    def merge_cross_source_duplicates(self, items: List[ContentItem]) -> List[ContentItem]:
        """Merge items that point to the same URL from different sources.

        This is a stable stage helper for integrations such as MCP.

        Keeps the item with the richest content and combines metadata.

        Args:
            items: Items to deduplicate

        Returns:
            List[ContentItem]: Deduplicated items
        """
        # Group by normalized URL
        url_groups: Dict[tuple[object, ...], List[ContentItem]] = {}
        for item in items:
            if isinstance(item.profile, list):
                requested_profile: object = tuple(
                    profile_id.strip() for profile_id in item.profile
                )
            else:
                requested_profile = (item.profile or "auto").strip() or "auto"
            key = (*_deduplication_item_key(item), requested_profile)
            url_groups.setdefault(key, []).append(item)

        merged = []
        for group in url_groups.values():
            group_copies = [item.model_copy(deep=True) for item in group]
            if len(group) == 1:
                merged.append(group_copies[0])
                continue

            # Pick the item with the richest content as primary
            primary = max(group_copies, key=lambda x: len(x.content or ""))

            # Merge metadata and source info from other items
            all_sources = []
            for item in group_copies:
                if item.source_type.value not in all_sources:
                    all_sources.append(item.source_type.value)
                # Merge metadata (engagement, discussion, etc.)
                for mk, mv in item.metadata.items():
                    if mk not in primary.metadata or not primary.metadata[mk]:
                        primary.metadata[mk] = mv

                # Append the other source's material. When either side carries
                # typed sections, merge those and let `content` be re-derived:
                # concatenating strings would desync the two, and the
                # concatenated tail would land in `claimable` untiered.
                if item is not primary and item.content:
                    if primary.sections or item.sections:
                        if not primary.sections:
                            primary.sections.extend(
                                marker_sections_to_model(primary.content)
                            )
                        primary.sections.extend(
                            list(item.sections) or marker_sections_to_model(item.content)
                        )
                        primary.rebuild_content()
                    elif primary.content and item.content not in primary.content:
                        primary.content = (primary.content or "") + (
                            f"\n\n--- From {item.source_type.value} ---\n" + item.content
                        )

            primary.metadata["merged_sources"] = all_sources
            merged.append(primary)

        return merged

    async def merge_topic_duplicates(
        self,
        items: List[ContentItem],
        *,
        log: bool = True,
    ) -> List[ContentItem]:
        """Merge items covering the same topic using AI semantic deduplication.

        This is a stable stage helper for integrations such as MCP.

        Sends all item titles, tags, and summaries to AI in a single call.
        Items must already be sorted by analysis score descending so that the first
        item in each duplicate group is always the highest-scored one.
        Content (comments) from duplicate items is merged into the primary.

        Falls back to returning items unchanged if the AI call fails.
        """
        if len(items) <= 1:
            return items

        from .ai.prompting.deduplication import TOPIC_DEDUP_SYSTEM, TOPIC_DEDUP_USER
        from .ai.utils import parse_json_response

        # Build the item list for the prompt
        lines = []
        for i, item in enumerate(items):
            analysis = item.processing.analysis if item.processing else None
            tags = ", ".join(analysis.tags) if analysis and analysis.tags else "—"
            summary = analysis.summary if analysis else "—"
            lines.append(f"[{i}] {item.title}\n    Tags: {tags}\n    Summary: {summary}")
        items_text = "\n\n".join(lines)

        try:
            ai_client = self._make_ai_client()
            response = await ai_client.complete(
                system=TOPIC_DEDUP_SYSTEM,
                user=TOPIC_DEDUP_USER.format(items=items_text),
            )
            result = parse_json_response(response)
            if result is None:
                if log:
                    self.console.print("[yellow]  dedup: could not parse AI response, skipping[/yellow]")
                return items

            duplicate_groups = result.get("duplicates", [])
        except Exception as e:
            if log:
                self.console.print(f"[yellow]  dedup: AI call failed ({e}), skipping[/yellow]")
            return items

        if not duplicate_groups:
            return items

        # Build a set of indices to drop (all non-primary duplicates)
        drop_indices: set[int] = set()
        for group in duplicate_groups:
            if not isinstance(group, list) or len(group) < 2:
                continue
            primary_idx = group[0]
            if primary_idx < 0 or primary_idx >= len(items):
                continue
            primary = items[primary_idx]
            for dup_idx in group[1:]:
                if not isinstance(dup_idx, int) or dup_idx < 0 or dup_idx >= len(items):
                    continue
                if dup_idx == primary_idx:
                    continue
                dup = items[dup_idx]
                # Merge comments/content from the duplicate into the primary
                if dup.content:
                    if not primary.content or dup.content not in primary.content:
                        label = dup.source_type.value
                        primary.content = (primary.content or "") + f"\n\n--- From {label} ---\n{dup.content}"
                if log:
                    self.console.print(
                        f"   [dim]dedup: keep [{primary_idx}] {primary.title}[/dim]\n"
                        f"   [dim]       drop [{dup_idx}] {dup.title}[/dim]"
                    )
                drop_indices.add(dup_idx)

        return [item for i, item in enumerate(items) if i not in drop_indices]

    async def filter_items(
        self,
        items: List[ContentItem],
        *,
        threshold: Optional[float] = None,
        topic_dedup: bool = True,
        apply_balance: bool = True,
        log: bool = True,
    ) -> FilteringPipelineResult:
        """Apply score thresholding, optional topic dedup, and digest balancing."""
        threshold_items = []
        for item in items:
            if self.passes_profile_filter(item, threshold):
                threshold_items.append(item)
        threshold_items.sort(
            key=lambda item: (
                item.processing.analysis.score
                if item.processing and item.processing.analysis and item.processing.analysis.score is not None
                else -1
            ),
            reverse=True,
        )

        if log:
            self.console.print(
                f"{self.icons['filter']} Selected {len(threshold_items)} items "
                "with profile filters\n"
            )

        deduped_items = threshold_items
        if topic_dedup and deduped_items:
            profile_groups: Dict[str, List[ContentItem]] = defaultdict(list)
            for item in deduped_items:
                profile_id = (
                    item.processing.classification.profile
                    if item.processing
                    else self.profiles.default_profile
                )
                profile_groups[profile_id].append(item)
            deduped_items = []
            for profile_id, profile_items in profile_groups.items():
                settings = self.config.processing.profile_settings.get(profile_id)
                if settings is None or settings.topic_dedup:
                    deduped_items.extend(
                        await self.merge_topic_duplicates(profile_items, log=log)
                    )
                else:
                    deduped_items.extend(profile_items)
            deduped_items.sort(
                key=lambda item: (
                    item.processing.analysis.score
                    if item.processing
                    and item.processing.analysis
                    and item.processing.analysis.score is not None
                    else -1
                ),
                reverse=True,
            )
        topic_dedup_removed = len(threshold_items) - len(deduped_items)

        if log and topic_dedup_removed:
            self.console.print(
                f"{self.icons['cleanup']} Removed {topic_dedup_removed} topic duplicates "
                f"→ {len(deduped_items)} unique items\n"
            )

        balanced_digest = (
            self.apply_balanced_digest(deduped_items, log=log)
            if apply_balance
            else BalancedDigestResult(items=deduped_items)
        )
        return FilteringPipelineResult(
            items=balanced_digest.items,
            threshold_count=len(threshold_items),
            topic_dedup_count=len(deduped_items),
            topic_dedup_removed=topic_dedup_removed,
            balanced_digest=balanced_digest,
        )

    async def select_digest_items(
        self,
        items: List[ContentItem],
        *,
        threshold: Optional[float] = None,
        topic_dedup: bool = True,
        log: bool = True,
    ) -> FilteringPipelineResult:
        """Select final digest items using the same stages for every entry point."""
        initial = await self.filter_items(
            items,
            threshold=threshold,
            topic_dedup=topic_dedup,
            apply_balance=False,
            log=log,
        )
        candidates = initial.items
        await self._expand_twitter_discussion(candidates)

        # Targeted re-analysis can lower a score, so reapply profile filters.
        eligible = [
            item
            for item in candidates
            if self.passes_profile_filter(item, threshold)
        ]
        eligible.sort(
            key=lambda item: (
                item.processing.analysis.score
                if item.processing
                and item.processing.analysis
                and item.processing.analysis.score is not None
                else -1
            ),
            reverse=True,
        )
        balanced = self.apply_balanced_digest(eligible, log=log)
        return FilteringPipelineResult(
            items=balanced.items,
            threshold_count=initial.threshold_count,
            topic_dedup_count=initial.topic_dedup_count,
            topic_dedup_removed=initial.topic_dedup_removed,
            balanced_digest=balanced,
            eligible_count=len(eligible),
        )

    def passes_profile_filter(
        self,
        item: ContentItem,
        threshold: Optional[float] = None,
    ) -> bool:
        if not item.processing or not item.processing.analysis:
            return False
        profile_id = item.processing.classification.profile
        settings = self.config.processing.profile_settings.get(profile_id)
        effective_threshold = threshold
        if effective_threshold is None and settings is not None:
            effective_threshold = settings.threshold
        if effective_threshold is None:
            return True
        score = item.processing.analysis.score
        return score is not None and score >= effective_threshold

    def apply_balanced_digest(
        self,
        items: List[ContentItem],
        *,
        log: bool = True,
    ) -> BalancedDigestResult:
        """Apply configured category quotas and the final item cap.

        Categories are read from ``item.metadata["category"]``. If a category
        appears in more than one configured group, the first group in config
        order wins.
        """
        digest = self.config.digest
        groups = digest.category_groups
        max_items = digest.max_items

        if not groups and max_items is None:
            return BalancedDigestResult(items=items)

        sorted_items = sorted(
            items,
            key=lambda item: (
                item.processing.analysis.score
                if item.processing and item.processing.analysis and item.processing.analysis.score is not None
                else -1
            ),
            reverse=True,
        )

        category_to_group: Dict[str, str] = {}
        duplicate_categories: List[str] = []
        for group_key, group in groups.items():
            for category in group.categories:
                if category in category_to_group:
                    if category_to_group[category] != group_key:
                        duplicate_categories.append(category)
                    continue
                category_to_group[category] = group_key

        if log:
            for category in sorted(set(duplicate_categories)):
                first_group = category_to_group[category]
                self.console.print(
                    f"[yellow]Warning: category '{category}' is configured in multiple "
                    f"groups; using '{first_group}'.[/yellow]"
                )

        selected: List[tuple[ContentItem, str]] = []
        group_counts: Dict[str, int] = defaultdict(int)
        default_group = digest.default_group

        for item in sorted_items:
            category = item.metadata.get("category")
            group_key = (
                category_to_group.get(category, default_group)
                if isinstance(category, str)
                else default_group
            )

            if group_key in groups:
                limit = groups[group_key].limit
            else:
                limit = digest.default_group_limit

            if limit is not None and group_counts[group_key] >= limit:
                continue

            selected.append((item, group_key))
            group_counts[group_key] += 1

        if max_items is not None:
            selected = selected[:max_items]

        final_counts: Dict[str, int] = defaultdict(int)
        for _, group_key in selected:
            final_counts[group_key] += 1

        group_limits: Dict[str, Optional[int]] = {
            group_key: group.limit for group_key, group in groups.items()
        }
        group_limits.setdefault(default_group, digest.default_group_limit)

        if log:
            self.console.print(
                f"{self.icons['balance']} Balanced digest selected "
                f"{len(selected)}/{len(items)} items"
            )
            for group_key, group in groups.items():
                label = group.name or group_key
                self.console.print(
                    f"      {self.icons['detail']} {label}: "
                    f"{final_counts.get(group_key, 0)}/{group.limit}"
                )
            if (
                final_counts.get(default_group, 0)
                or digest.default_group_limit is not None
            ):
                limit_label = (
                    str(digest.default_group_limit)
                    if digest.default_group_limit is not None
                    else "unlimited"
                )
                self.console.print(
                    f"      {self.icons['detail']} {default_group}: "
                    f"{final_counts.get(default_group, 0)}/{limit_label}"
                )
            self.console.print("")

        return BalancedDigestResult(
            items=[item for item, _ in selected],
            enabled=True,
            group_counts=dict(final_counts),
            group_limits=group_limits,
            duplicate_categories=sorted(set(duplicate_categories)),
        )

    async def _expand_twitter_discussion(self, items: List[ContentItem]) -> None:
        """Second-stage: fetch reply text for important Twitter items and re-analyze.

        Only runs when sources.twitter.fetch_reply_text is True.
        Bounded by max_tweets_to_expand to control cost.
        """
        tw_cfg = self.config.sources.twitter
        if not tw_cfg or not tw_cfg.enabled or not tw_cfg.fetch_reply_text:
            return

        from .models import SourceType

        twitter_items = [
            item for item in items
            if item.source_type == SourceType.TWITTER
        ][:tw_cfg.max_tweets_to_expand]

        if not twitter_items:
            return

        self.console.print(
            f"{self.icons['discussion']} Fetching reply text for "
            f"{len(twitter_items)} Twitter items..."
        )

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            if tw_cfg.mode == "playwright":
                self.console.print(
                    "   [yellow]Reply expansion not yet supported in Playwright mode.[/yellow]"
                )
                return
            scraper = TwitterScraper(tw_cfg, client)
            expanded = []
            for item in twitter_items:
                try:
                    reply_sections = await scraper.fetch_replies_for_item(item)
                    if TwitterScraper.append_discussion_sections(item, reply_sections):
                        expanded.append(item)
                        self.console.print(
                            f"   {self.icons['discussion']} {len(reply_lines)} replies "
                            f"added to: {item.title[:60]}"
                        )
                except Exception as exc:
                    self.console.print(
                        f"   [yellow]{self.icons['warning']} Reply fetch failed for "
                        f"{item.id}: {exc}[/yellow]"
                    )

        if not expanded:
            return

        self.console.print(
            f"   Re-analyzing {len(expanded)} Twitter items with reply context...\n"
        )
        ai_client = self._make_ai_client()
        analyzer = ContentAnalyzer(ai_client, self.profiles, console=self.console)
        await analyzer.analyze_batch(expanded)

    async def enrich_items(self, items: List[ContentItem]) -> EnrichmentBatchResult:
        """Enrich items with background knowledge (2nd AI pass).

        For each item that passed the score threshold, call AI to generate
        background knowledge based on the item's actual content.

        Args:
            items: Important items to enrich (modified in-place)
        """
        if not items:
            return EnrichmentBatchResult()

        self.console.print(
            f"{self.icons['enrich']} Enriching with background knowledge..."
        )
        ai_client = self._make_ai_client()
        enricher = ContentEnricher(
            ai_client,
            self.profiles,
            self.config.ai.languages,
            console=self.console,
            tools=ToolRegistry(self.storage.summaries_dir),
        )
        result = await enricher.enrich_batch(items)
        self.console.print(
            f"   Enriched {result.succeeded_count}/{len(items)} items"
        )
        if result.failed_count:
            self.console.print(
                f"   [yellow]Skipped {result.failed_count} items after enrichment "
                f"failed: {', '.join(result.failed_ids)}[/yellow]"
            )
        self.console.print("")
        return result

    async def analyze_items(self, items: List[ContentItem]) -> List[ContentItem]:
        """Analyze content items with AI.

        Args:
            items: Items to analyze

        Returns:
            List[ContentItem]: Analyzed items
        """
        self.console.print(f"{self.icons['ai']} Analyzing content with AI...")

        ai_client = self._make_ai_client()
        analyzer = ContentAnalyzer(ai_client, self.profiles, console=self.console)

        return await analyzer.analyze_batch(items)

    async def _generate_summary(
        self,
        items: List[ContentItem],
        date: str,
        total_fetched: int,
        language: str = "en",
    ) -> str:
        """Generate daily summary.

        Args:
            items: Important items to include (already enriched with background/related)
            date: Date string
            total_fetched: Total items fetched
            language: Output language ("en" or "zh")

        Returns:
            str: Markdown summary
        """
        self.console.print(f"{self.icons['summary']} Generating daily summary...")

        summarizer = DailySummarizer(
            profile_names=self.profiles.names,
            profile_order=self.config.digest.profile_order,
        )

        return await summarizer.generate_summary(items, date, total_fetched, language=language)
