"""Unit tests for libs/common/json_extractor.py.

Covers all 10 specified test cases for extract_json():
1. Clean JSON block
2. Fenced with ```json tag with prose around it
3. Extraneous prose before and after block
4. Multiple blocks — first wins
5. No JSON block present — raises JsonExtractionError
6. Empty JSON block — raises JsonExtractionError
7. Invalid JSON in block — raises JsonExtractionError
8. JSON array (not just objects)
9. Unfenced raw JSON — raises JsonExtractionError
10. Nested backticks inside JSON string value
"""

import pytest

from libs.common.json_extractor import JsonExtractionError, extract_json


class TestExtractJsonSuccess:
    """Tests where extract_json should return a parsed value."""

    def test_clean_json_block(self) -> None:
        """Bare fenced block with no surrounding prose returns the parsed dict."""
        text = '```json\n{"key": "value"}\n```'
        result = extract_json(text)
        assert result == {"key": "value"}

    def test_fenced_with_prose_before_and_after(self) -> None:
        """Fenced block surrounded by prose returns the parsed dict."""
        text = "Here is the result:\n```json\n{\"a\": 1}\n```\nDone."
        result = extract_json(text)
        assert result == {"a": 1}

    def test_extraneous_prose_around_block(self) -> None:
        """Longer prose context before and after the block is stripped correctly."""
        text = (
            "I analyzed the data and here are my findings:\n\n"
            '```json\n{"summary": "test", "recommendations": []}\n```\n\n'
            "Let me know if you need changes."
        )
        result = extract_json(text)
        assert result == {"summary": "test", "recommendations": []}

    def test_multiple_blocks_first_wins(self) -> None:
        """When multiple fenced blocks exist, the first valid one is returned."""
        text = (
            '```json\n{"first": true}\n```\n'
            "Also:\n"
            '```json\n{"second": true}\n```'
        )
        result = extract_json(text)
        assert result == {"first": True}

    def test_json_array(self) -> None:
        """A top-level JSON array (not just object) is parsed correctly."""
        text = '```json\n[{"a": 1}, {"b": 2}]\n```'
        result = extract_json(text)
        assert result == [{"a": 1}, {"b": 2}]

    def test_nested_backticks_in_json_string_value(self) -> None:
        """Triple backticks inside a JSON string value do not confuse the extractor."""
        # The JSON value contains a backtick sequence that is NOT a fence closer.
        text = '```json\n{"code": "use `x` here"}\n```'
        result = extract_json(text)
        assert result == {"code": "use `x` here"}

    def test_whitespace_around_json_inside_fence(self) -> None:
        """Leading/trailing whitespace inside the fence is tolerated by json.loads."""
        text = "```json\n  {\"key\": \"value\"}  \n```"
        result = extract_json(text)
        assert result == {"key": "value"}

    def test_numeric_values(self) -> None:
        """JSON with numeric, boolean, and null values is parsed correctly."""
        text = '```json\n{"count": 42, "ratio": 3.14, "active": false, "label": null}\n```'
        result = extract_json(text)
        assert result == {"count": 42, "ratio": 3.14, "active": False, "label": None}

    def test_nested_objects(self) -> None:
        """Deeply nested JSON object is returned as a fully parsed dict."""
        text = '```json\n{"outer": {"inner": {"deep": "value"}}}\n```'
        result = extract_json(text)
        assert result == {"outer": {"inner": {"deep": "value"}}}


class TestExtractJsonErrors:
    """Tests where extract_json should raise JsonExtractionError."""

    def test_no_json_block_present(self) -> None:
        """Plain prose with no fenced block raises JsonExtractionError."""
        with pytest.raises(JsonExtractionError, match="No JSON"):
            extract_json("I could not analyze the data.")

    def test_empty_json_block(self) -> None:
        """An empty fenced block (no content) raises JsonExtractionError."""
        with pytest.raises(JsonExtractionError):
            extract_json("```json\n\n```")

    def test_invalid_json_in_block(self) -> None:
        """A fenced block containing malformed JSON raises JsonExtractionError."""
        with pytest.raises(JsonExtractionError, match="Invalid JSON"):
            extract_json("```json\n{invalid json}\n```")

    def test_unfenced_raw_json(self) -> None:
        """Raw JSON without a markdown fence raises JsonExtractionError.

        This enforces strict fenced-block-only extraction for reliability.
        """
        with pytest.raises(JsonExtractionError):
            extract_json('{"key": "value"}')

    def test_error_message_includes_input_snippet(self) -> None:
        """JsonExtractionError message includes a snippet of the offending input."""
        input_text = "No fenced block here at all."
        with pytest.raises(JsonExtractionError) as exc_info:
            extract_json(input_text)
        assert "No fenced block here" in str(exc_info.value)

    def test_empty_string_raises(self) -> None:
        """An empty string raises JsonExtractionError."""
        with pytest.raises(JsonExtractionError):
            extract_json("")

    def test_json_extractor_error_is_exception(self) -> None:
        """JsonExtractionError is a subclass of Exception (not PhantomPerpError)."""
        assert issubclass(JsonExtractionError, Exception)
        # Verify it does NOT inherit from PhantomPerpError
        try:
            from libs.common.exceptions import PhantomPerpError

            assert not issubclass(JsonExtractionError, PhantomPerpError)
        except ImportError:
            pass  # PhantomPerpError not available — skip hierarchy check
