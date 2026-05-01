"""Tests for structured traceback serialization.

Verifies that serialize_traceback() produces valid JSON-compatible dicts
with the expected shape for each exception type.
"""

import json

import pytest

from atopile.errors import UserException, UserKeyError, UserTypeError

# ---------------------------------------------------------------------------
# Unit tests for the base UserException
# ---------------------------------------------------------------------------


class TestUserExceptionSerialize:
    """Base UserException.serialize_traceback() produces the right envelope."""

    def test_basic_shape(self):
        exc = UserException("something went wrong")
        tb = exc.serialize_traceback()

        assert isinstance(tb, dict)
        assert set(tb.keys()) == {"title", "message", "frames", "origin"}

    def test_message_preserved(self):
        exc = UserException("voltage out of range")
        tb = exc.serialize_traceback()

        assert tb["message"] == "voltage out of range"

    def test_title_derived_from_class(self):
        exc = UserException("msg")
        tb = exc.serialize_traceback()

        # UserException → "Exception" (strips "User" prefix and titlecases)
        assert tb["title"] == "Exception"

    def test_custom_title(self):
        exc = UserException("msg", title="Custom Title")
        tb = exc.serialize_traceback()

        assert tb["title"] == "Custom Title"

    def test_frames_empty_for_base(self):
        exc = UserException("msg")
        tb = exc.serialize_traceback()

        assert tb["frames"] == []

    def test_origin_none_for_base(self):
        exc = UserException("msg")
        tb = exc.serialize_traceback()

        assert tb["origin"] is None

    def test_json_serializable(self):
        exc = UserException("something broke")
        tb = exc.serialize_traceback()

        # Must survive a round-trip through JSON
        serialized = json.dumps(tb)
        restored = json.loads(serialized)
        assert restored == tb

    def test_subclass_title_strips_user_prefix(self):
        exc = UserKeyError("field not found")
        tb = exc.serialize_traceback()

        assert tb["title"] == "Key Error"

    def test_subclass_preserves_message(self):
        exc = UserTypeError("expected int, got str")
        tb = exc.serialize_traceback()

        assert tb["message"] == "expected int, got str"


# ---------------------------------------------------------------------------
# Integration tests with real compiler errors
# ---------------------------------------------------------------------------


class TestDslExceptionSerialize:
    """DslRichException.serialize_traceback() from real compiler errors."""

    def test_undefined_field_error(self):
        """A field reference error should serialize with frames and origin."""
        from atopile.compiler import DslException
        from test.compiler.conftest import build_instance

        with pytest.raises(DslException) as exc_info:
            build_instance(
                """
            module C:
                pass

            module B:
                Bs = new C[2]

            module A:
                b = new B
                b[5] = 5V

            module App:
                a = new A
            """,
                "App",
            )

        exc = exc_info.value
        # The exception wraps as DslRichException for the UI
        from atopile.compiler import DslRichException

        rich_exc = DslRichException(
            message=exc.message,
            original=exc,
            source_node=exc.source_node if hasattr(exc, "source_node") else None,
        )
        tb = rich_exc.serialize_traceback()

        assert isinstance(tb, dict)
        assert tb["message"] == exc.message
        assert isinstance(tb["frames"], list)
        # Must be JSON-serializable
        json.dumps(tb)

    def test_invalid_pragma_error(self):
        """An invalid pragma should produce a serializable traceback."""
        from atopile.compiler import DslException
        from test.compiler.conftest import build_instance

        with pytest.raises(DslException) as exc_info:
            build_instance(
                """
            #pragma experiment("INVALID_EXPERIMENT")

            module App:
                pass
            """,
                "App",
            )

        exc = exc_info.value
        from atopile.compiler import DslRichException

        rich_exc = DslRichException(
            message=exc.message,
            original=exc,
            source_node=exc.source_node if hasattr(exc, "source_node") else None,
        )
        tb = rich_exc.serialize_traceback()

        assert isinstance(tb, dict)
        assert "frames" in tb
        assert "origin" in tb
        json.dumps(tb)

    def test_validation_error_serialize(self):
        """A validation/resolution error should serialize with source info."""
        from atopile.compiler import DslException
        from test.compiler.conftest import build_instance

        with pytest.raises(DslException) as exc_info:
            build_instance(
                """
            module App:
                signal s1
                power.missing = 5V
            """,
                "App",
            )

        exc = exc_info.value
        from atopile.compiler import DslRichException

        rich_exc = DslRichException(
            message=exc.message,
            original=exc,
            source_node=exc.source_node if hasattr(exc, "source_node") else None,
        )
        tb = rich_exc.serialize_traceback()

        assert isinstance(tb, dict)
        assert tb["message"] == exc.message
        json.dumps(tb)


# ---------------------------------------------------------------------------
# Source frame shape validation
# ---------------------------------------------------------------------------


class TestSourceFrameShape:
    """When origin/frames are present, they have the right structure."""

    EXPECTED_FRAME_KEYS = {
        "file",
        "line",
        "column",
        "code",
        "start_line",
        "highlight_lines",
    }

    def test_origin_frame_shape(self):
        """Origin frames from DslRichException have all required fields."""
        from atopile.compiler import DslException, DslRichException
        from test.compiler.conftest import build_instance

        with pytest.raises(DslException) as exc_info:
            build_instance(
                """
            module App:
                signal s1
                power.missing = 5V
            """,
                "App",
            )

        exc = exc_info.value
        rich_exc = DslRichException(
            message=exc.message,
            original=exc,
            source_node=exc.source_node if hasattr(exc, "source_node") else None,
        )
        tb = rich_exc.serialize_traceback()

        # If origin is present, check its shape
        if tb["origin"] is not None:
            assert set(tb["origin"].keys()) == self.EXPECTED_FRAME_KEYS
            assert isinstance(tb["origin"]["file"], str)
            assert isinstance(tb["origin"]["line"], int)
            assert isinstance(tb["origin"]["code"], str)
            assert isinstance(tb["origin"]["highlight_lines"], list)

    def test_full_json_roundtrip(self):
        """The complete traceback survives JSON encode → decode → re-encode."""
        from atopile.compiler import DslException, DslRichException
        from test.compiler.conftest import build_instance

        with pytest.raises(DslException) as exc_info:
            build_instance(
                """
            module C:
                pass

            module B:
                Bs = new C[2]

            module A:
                b = new B
                b[5] = 5V

            module App:
                a = new A
            """,
                "App",
            )

        exc = exc_info.value
        rich_exc = DslRichException(
            message=exc.message,
            original=exc,
            source_node=exc.source_node if hasattr(exc, "source_node") else None,
        )
        tb = rich_exc.serialize_traceback()

        # Round-trip twice to be sure
        s1 = json.dumps(tb)
        d1 = json.loads(s1)
        s2 = json.dumps(d1)
        assert s1 == s2
