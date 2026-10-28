# Stack Overflow Question and Answer Enrichment

## Intent

Extend Stack Overflow search hits with a current question body, full answer bodies, and comments attached to the question or the corresponding answer. Keep this evidence in the same investigation and corpus path used by other platform enrichers.

## Chosen approach

Use the public Stack Exchange API: fetch the question and its vote-sorted answers, then fetch comments for the question and selected answer IDs in one posts-comments request. This fits the existing question search source and avoids browser use. API `withbody` supplies body fields; the adapter strips HTML into searchable plain text.

The alternative was to follow the question's HTML page and parse rendered answers, which adds browser/page fragility while the public API already exposes these objects. Reading only the question's `answer_count` would leave the answer content unavailable, so it does not meet the collection goal.

## Constraints

- Keep at most 10 answers and 60 comments per question; use only the remaining round budget.
- Sort answers by votes and retain an accepted answer when it is present in the fetched page.
- Fetch at most one page of answers and one page of comments; expose `has_more` and truncation in coverage.
- Group question comments under the question and answer comments under their answer.
- Preserve `reply_to_user` as a field. The API does not return a parent comment ID, so do not invent a comment reply tree.
- Keep all answers and comments classified as community material.

## Acceptance

Tests cover search ID preservation, body extraction, answer/comment association, timeout partial results, missing question state, TopicService budget propagation, and run-shard / SQLite retrieval. A bounded anonymous live sample records counts only.

## Official API references

- [Questions by ID](https://api.stackexchange.com/docs/questions-by-ids)
- [Answers on questions](https://api.stackexchange.com/docs/answers-on-questions)
- [Comments on posts](https://api.stackexchange.com/docs/comments-on-posts)
- [Custom filters and `withbody`](https://api.stackexchange.com/docs/filters)
