from types import SimpleNamespace

from skye.citations import sanitize_citations, url_citations

CITE_TURN0_VIEW0 = "citeturn0view0"
CITE_TURN0_SEARCH0 = "\ue200cite\ue202turn0search0\ue201"
CITE_MULTI = "\ue200cite\ue202turn0search0\ue202turn0search1\ue201"
CITE_FILE = "\ue200cite\ue202turn0file0\ue202L8-L13\ue201"


def test_exact_user_cite_token_is_removed() -> None:
    assert CITE_TURN0_VIEW0 == "\ue200cite\ue202turn0view0\ue201"
    assert sanitize_citations(CITE_TURN0_VIEW0) == ""
    assert sanitize_citations(f"Paris is the capital.{CITE_TURN0_VIEW0}") == "Paris is the capital."


def test_sibling_citation_wrappers_are_removed() -> None:
    assert sanitize_citations(f"Done.{CITE_TURN0_SEARCH0}") == "Done."
    assert sanitize_citations(f"Done.{CITE_MULTI}") == "Done."
    assert sanitize_citations(f"See the clause.{CITE_FILE}") == "See the clause."
    assert sanitize_citations("Answer 【turn0search0】 here.") == "Answer here."
    assert sanitize_citations("Older 【0†example.com】 form.") == "Older form."
    assert sanitize_citations("Bare turn0view0 leftover.") == "Bare leftover."
    assert sanitize_citations("cite turn0view0 still leaks.") == "still leaks."
    assert sanitize_citations("citeturn0search2 glued.") == "glued."
    unterminated = "Start \ue200cite\ue202turn0view0 and more."
    assert sanitize_citations(unterminated) == "Start and more."
    mixed = "Keep this.\ue200cite\ue202turn0view0 real text \ue200cite\ue202turn0search0\ue201"
    assert sanitize_citations(mixed).strip() == "Keep this. real text"
    assert sanitize_citations("Keep \ue200hello") == "Keep hello"


def test_https_url_in_the_same_sentence_survives() -> None:
    text = f"See https://example.com/report {CITE_TURN0_VIEW0} for the source."
    assert sanitize_citations(text) == "See https://example.com/report for the source."
    linked = f"Read [the report](https://example.com/report){CITE_TURN0_SEARCH0}."
    assert sanitize_citations(linked) == "Read [the report](https://example.com/report)."
    path = "Keep https://example.com/turn0view0/docs intact."
    assert sanitize_citations(path) == path


def test_english_cite_and_plain_text_are_kept() -> None:
    text = "Please cite your sources."
    assert sanitize_citations(text) == text
    assert sanitize_citations("  Ready.  ") == "  Ready.  "


def test_known_annotation_url_replaces_the_token() -> None:
    token = CITE_TURN0_VIEW0
    text = f"Paris is the capital. {token}"
    start = text.index(token)
    cleaned = sanitize_citations(
        text,
        annotations=[
            SimpleNamespace(
                type="url_citation",
                start_index=start,
                end_index=start + len(token),
                url="https://example.com/paris",
                title="Paris",
            )
        ],
    )
    assert cleaned == "Paris is the capital. [Paris](https://example.com/paris)"
    assert "cite" not in cleaned
    assert "turn0view0" not in cleaned


def test_missing_or_non_https_annotations_drop_the_token() -> None:
    token = CITE_TURN0_VIEW0
    text = f"Paris is the capital. {token}"
    start = text.index(token)
    assert sanitize_citations(text) == "Paris is the capital."
    assert (
        sanitize_citations(
            text,
            annotations=[
                SimpleNamespace(
                    type="url_citation",
                    start_index=start,
                    end_index=start + len(token),
                    url="http://insecure.example/paris",
                    title="Paris",
                )
            ],
        )
        == "Paris is the capital."
    )
    paragraph = "Paris is the capital of France."
    assert (
        sanitize_citations(
            paragraph,
            annotations=[
                SimpleNamespace(
                    type="url_citation",
                    start_index=0,
                    end_index=len(paragraph),
                    url="https://example.com/paris",
                    title="Paris",
                )
            ],
        )
        == paragraph
    )


def test_url_citations_are_collected_from_hosted_search_output() -> None:
    annotation = SimpleNamespace(
        type="url_citation",
        start_index=10,
        end_index=20,
        url="https://example.com/paris",
        title="Paris",
    )
    result = SimpleNamespace(
        raw_responses=[
            SimpleNamespace(
                output=[
                    SimpleNamespace(
                        type="message",
                        content=[SimpleNamespace(type="output_text", annotations=[annotation])],
                    )
                ]
            )
        ],
        new_items=[],
    )
    found = url_citations(result)
    assert found == (annotation,)
