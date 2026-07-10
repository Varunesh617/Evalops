"""Example: building a custom metric plugin with the EvalOps SDK."""

from evalops_sdk.metrics import MetricAuthoring

# ---------------------------------------------------------------------------
# Define a "keyword coverage" metric using the fluent API
# ---------------------------------------------------------------------------

metric = (
    MetricAuthoring.define("example.keyword_coverage", "Keyword Coverage")
    .version("0.1.0")
    .author("EvalOps Examples")
    .description("Measures what fraction of query keywords appear in the output.")
    .tags("keyword", "coverage", "retrieval")
    .config(min_keyword_count=2)
)


def _keyword_scorer(input_text: str, output_text: str) -> float:
    input_words = set(input_text.lower().split())
    output_words = set(output_text.lower().split())
    if not input_words:
        return 1.0
    covered = input_words & output_words
    return len(covered) / len(input_words)


metric.scorer(_keyword_scorer, description="keyword overlap ratio")

# ---------------------------------------------------------------------------
# Quick run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    result = metric.evaluate_pair(
        "What are the side effects of metformin?",
        "Metformin may cause nausea, diarrhea, and stomach pain.",
    )
    print(f"Overall: {result['overall']:.3f}")
    for r in result["scorer_results"]:
        print(f"  {r['description']}: {r['score']:.3f}")
