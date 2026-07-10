"""Example: building a custom filter plugin with the EvalOps SDK."""

from evalops_sdk.filters import FilterAuthoring, FilterVerdict

# ---------------------------------------------------------------------------
# Define a "profanity" filter using the fluent API
# ---------------------------------------------------------------------------

_BLOCKED_TERMS = {"damn", "hell", "crap"}


def _profanity_check(
    input_text: str, context: str, output_text: str
) -> tuple[FilterVerdict, float, dict]:
    target = (output_text or input_text).lower()
    words = set(target.split())
    hits = words & _BLOCKED_TERMS
    if not hits:
        return FilterVerdict.ALLOW, 0.0, {"hits": []}
    score = min(1.0, len(hits) / 5.0)
    if score >= 0.6:
        return FilterVerdict.BLOCK, score, {"hits": sorted(hits)}
    return FilterVerdict.WARN, score, {"hits": sorted(hits)}


filter_plugin = (
    FilterAuthoring.define("example.profanity_filter", "Profanity Filter")
    .version("0.1.0")
    .author("EvalOps Examples")
    .description("Detects and optionally blocks profane language in outputs.")
    .threshold(0.6)
    .check(_profanity_check, description="simple word-list scan")
    .config(extra_blocked_words=[])
)

# ---------------------------------------------------------------------------
# Quick run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    clean = filter_plugin.run_checks(
        "Tell me about dogs",
        output="Dogs are wonderful companions.",
    )
    print(f"Clean: verdict={clean['verdict']}, score={clean['score']:.3f}")

    dirty = filter_plugin.run_checks(
        "Say something rude",
        output="That was damn terrible and full of crap.",
    )
    print(f"Dirty: verdict={dirty['verdict']}, score={dirty['score']:.3f}")
    for c in dirty["checks"]:
        print(f"  {c['description']}: {c['verdict']} ({c['score']:.3f})")
