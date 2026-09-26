from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from skills.trtc.tools import reporting


class ReportingDocsQueryTests(unittest.TestCase):
    def _load(self, body: str):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".docs-query.yaml"
            path.write_text(body, encoding="utf-8")
            return reporting.load_docs_query_yaml(path)

    def test_restricted_yaml_common_schema(self) -> None:
        value = self._load(
            'sessionId: "sess_12345678"\n'
            'sdkappid: 1400000001\n'
            'platform: "web"\n'
            'types: ["restapi", "sdk"]\n'
            'lastPrompt: "how: login # literal"\n'
            'lastAnswer: |\n'
            '  First line\n'
            '\n'
            '  中文😀\\path\n'
        )
        self.assertEqual(value["types"], ["restapi", "sdk"])
        self.assertEqual(value["sdkappid"], 1400000001)
        self.assertIn("First line\n\n中文", value["lastAnswer"])

    def test_types_empty_and_null_numeric_scalars(self) -> None:
        value = self._load("sessionId: null\nsdkappid: 0\nplatform: flutter\ntypes: []\nlastPrompt: q\nlastAnswer: |\n  a\n")
        self.assertIsNone(value["sessionId"])
        self.assertEqual(value["sdkappid"], 0)
        self.assertEqual(value["types"], [])

    def test_block_scalar_dedents_by_minimum_indent(self) -> None:
        value = self._load("lastAnswer: |\n    first\n      nested\n    tail\nlastPrompt: q\n")
        self.assertEqual(value["lastAnswer"], "first\n  nested\ntail\n")

    def test_duplicate_unknown_and_malformed_fields_rejected(self) -> None:
        for body in (
            "platform: web\nplatform: ios\n",
            "unknown: x\n",
            "  nested: x\n",
            'platform: "unterminated\n',
        ):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self._load(body)

    def test_payload_preserves_arbitrary_legacy_event_text(self) -> None:
        payload = reporting.payload_from_docs_query(
            {"sessionId": "sess_12345678", "sdkappid": 0, "platform": "web", "types": []},
            method="event", text="skill_start|path=A",
        )
        self.assertEqual(payload["method"], "event")
        self.assertEqual(payload["text"], "skill_start|path=A")

    def test_prompt_requires_answer_and_feedback_requires_value(self) -> None:
        with self.assertRaisesRegex(ValueError, "lastAnswer"):
            reporting.payload_from_docs_query({}, method="prompt")
        with self.assertRaisesRegex(ValueError, "feedback"):
            reporting.payload_from_docs_query({}, method="feedback")

    def test_framework_comes_only_from_platform(self) -> None:
        self.assertEqual(reporting.derive_framework_from_docs_query("flutter", ["sdk"]), "flutter")
        self.assertEqual(reporting.derive_framework_from_docs_query("", ["restapi"]), "unknown")


if __name__ == "__main__":
    unittest.main()
