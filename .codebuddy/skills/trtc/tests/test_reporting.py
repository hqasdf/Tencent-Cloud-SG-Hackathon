from __future__ import annotations

import json
import io
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from skills.trtc.tools import reporting


class ReportingShimContractTests(unittest.TestCase):
    def test_production_shim_has_no_yaml_redaction_or_network_stack(self) -> None:
        source = Path(reporting.__file__).read_text(encoding="utf-8")
        self.assertNotIn("import yaml", source)
        self.assertNotIn("import re", source)
        self.assertNotIn("urllib", source)
        self.assertNotIn("requests", source)
        self.assertNotIn("import mcp", source.lower())
        self.assertNotIn("tencent-rtc-skill-tool", source.lower())

    def test_prompt_and_answer_are_stdin_only_for_node(self) -> None:
        calls = []

        class FakeStdin:
            def write(self, value): calls.append(("stdin", value))
            def close(self): pass

        class FakeStdout:
            def read(self, _size):
                if getattr(self, "done", False): return b""
                self.done = True
                return b'{"status":"preview"}'

        class FakeProc:
            returncode = 0
            stdin = FakeStdin(); stdout = FakeStdout(); stderr = None
            def wait(self, timeout=None): return 0
            def poll(self): return 0

        def fake_popen(argv, **_kwargs):
            calls.append(("argv", argv))
            return FakeProc()

        with mock.patch.object(reporting, "Popen", fake_popen), mock.patch.object(reporting, "_bundle_path", return_value=Path(__file__)):
            ok, _ = reporting._run_node("send", payload={"text": "prompt-secret", "answer": "answer-secret"})
        self.assertTrue(ok)
        argv = calls[0][1]
        self.assertNotIn("prompt-secret", " ".join(argv))
        self.assertNotIn("answer-secret", " ".join(argv))
        self.assertIn(b"prompt-secret", calls[1][1])

    def test_runtime_failure_is_fail_open_and_quiet(self) -> None:
        with mock.patch.object(reporting, "_bundle_path", return_value=Path("/missing/runtime.cjs")):
            self.assertEqual(reporting.main(["prompt", "--text", "hello"]), 0)

    def test_foreground_prompt_runtime_failure_is_nonzero_and_diagnostic(self) -> None:
        with mock.patch.object(reporting, "_bundle_path", return_value=Path("/missing/runtime.cjs")), \
             mock.patch("sys.stdin", io.TextIOWrapper(io.BytesIO(b'{"text":"hello"}'))), \
             mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
            self.assertEqual(reporting.main(["prompt", "--input-stdin", "--require-input"]), 1)
        self.assertIn("TRTC_REPORTING_RUNTIME_ERROR: runtime_unavailable", stderr.getvalue())

    def test_foreground_prompt_stage_timeout_is_not_fail_open(self) -> None:
        """A timeout before the durable event id is known must be visible."""
        with mock.patch.object(reporting, "_run_node", return_value=(False, {"status": "timeout"})), \
             mock.patch("sys.stdin", io.TextIOWrapper(io.BytesIO(b'{"text":"slow endpoint"}'))), \
             mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
            self.assertEqual(reporting.main(["prompt", "--input-stdin", "--require-input"]), 1)
        self.assertIn("TRTC_REPORTING_RUNTIME_ERROR: timeout", stderr.getvalue())

    def test_foreground_prompt_send_timeout_is_queued_fail_open(self) -> None:
        """Only the send phase may fail open, after stage returned an event id."""
        calls = []

        def fake_run_node(command, args=None, payload=None, **_kwargs):
            calls.append((command, args, payload))
            if command == "stage-prompt":
                return True, {"status": "staged", "event_id": "event-12345678"}
            return False, {"status": "timeout", "phase": "send", "event_id": "event-12345678"}

        with mock.patch.object(reporting, "_run_node", fake_run_node), \
             mock.patch("sys.stdin", io.TextIOWrapper(io.BytesIO(b'{"text":"slow endpoint"}'))), \
             mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
            self.assertEqual(reporting.main(["prompt", "--input-stdin", "--require-input"]), 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual([call[0] for call in calls], ["stage-prompt", "invoke"])
        self.assertIn("event-12345678", calls[1][1])

    def test_foreground_stage_without_event_id_is_not_silent_success(self) -> None:
        """A runtime result without a durable identity cannot enter send fail-open."""
        with mock.patch.object(reporting, "_run_node", return_value=(True, {"status": "staged"})), \
             mock.patch("sys.stdin", io.TextIOWrapper(io.BytesIO(b'{"text":"missing id"}'))), \
             mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
            self.assertEqual(reporting.main(["prompt", "--input-stdin", "--require-input"]), 1)
        self.assertIn("TRTC_REPORTING_RUNTIME_ERROR: runtime_failed", stderr.getvalue())

    def test_foreground_phases_share_one_absolute_deadline(self) -> None:
        """Stage and send use one total budget rather than two independent waits."""
        calls = []

        def fake_run_node(command, args=None, payload=None, **kwargs):
            calls.append((command, kwargs.get("timeout_ms"), kwargs.get("deadline")))
            if command == "stage-prompt":
                return True, {"status": "staged", "event_id": "event-shared-deadline"}
            return True, {"status": "promoted", "event_id": "event-shared-deadline"}

        with mock.patch.object(reporting, "_run_node", fake_run_node), \
             mock.patch("sys.stdin", io.TextIOWrapper(io.BytesIO(b'{"text":"shared deadline"}'))):
            self.assertEqual(reporting.main(["prompt", "--input-stdin", "--require-input"]), 0)
        self.assertEqual([call[0] for call in calls], ["stage-prompt", "invoke"])
        self.assertIs(calls[0][2], calls[1][2])
        self.assertLessEqual(calls[0][1], reporting.FOREGROUND_STAGE_BUDGET_MS)
        self.assertGreater(calls[1][1], 0)

    def test_foreground_prompt_requests_bounded_send_from_node_runtime(self) -> None:
        calls = []

        def fake_run_node(command, args=None, payload=None, **_kwargs):
            calls.append((command, args, payload))
            if command == "stage-prompt":
                return True, {"status": "staged", "event_id": "event-12345678"}
            return True, {"status": "promoted", "event_id": "event-12345678"}

        with mock.patch.object(reporting, "_run_node", fake_run_node), \
             mock.patch("sys.stdin", io.TextIOWrapper(io.BytesIO(b'{"text":"first prompt"}'))):
            self.assertEqual(reporting.main(["prompt", "--input-stdin", "--require-input"]), 0)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0], "stage-prompt")
        self.assertEqual(calls[0][1], [])
        self.assertEqual(calls[0][2]["text"], "first prompt")
        self.assertEqual(calls[1][0], "invoke")
        self.assertEqual(calls[1][1][:2], ["--event-id", "event-12345678"])
        self.assertIn("--skip-install-recovery", calls[1][1])

    def test_foreground_ambiguous_result_is_not_silent_success(self) -> None:
        def fake_run_node(command, args=None, payload=None, **_kwargs):
            return True, {"status": "ambiguous", "error": "session_binding_ambiguous"}

        with mock.patch.object(reporting, "_run_node", fake_run_node), \
             mock.patch("sys.stdin", io.TextIOWrapper(io.BytesIO(b'{"text":"prompt"}'))), \
             mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
            self.assertEqual(reporting.main(["prompt", "--input-stdin", "--require-input"]), 1)
        self.assertIn("TRTC_REPORTING_RUNTIME_ERROR: ambiguous", stderr.getvalue())

    def test_foreground_notice_marker_survives_ambiguous_prompt_failure(self) -> None:
        """A pending first-use notice must not be swallowed by a current retry."""
        def fake_run_node(command, args=None, payload=None, **_kwargs):
            return True, {
                "status": "ambiguous",
                "error": "session_binding_ambiguous",
                "marker": "TRTC_REPORTING_NOTICE_REQUIRED_V1",
                "notice": {"status": "created", "recovered": True},
            }

        stdout = io.StringIO()
        with mock.patch.object(reporting, "_run_node", fake_run_node), \
             mock.patch("sys.stdin", io.TextIOWrapper(io.BytesIO(b'{"text":"prompt"}'))), \
             mock.patch("sys.stderr", new_callable=io.StringIO) as stderr, \
             redirect_stdout(stdout):
            self.assertEqual(reporting.main(["prompt", "--input-stdin", "--require-input"]), 1)
        self.assertEqual(stdout.getvalue(), "TRTC_REPORTING_NOTICE_REQUIRED_V1\n")
        self.assertIn("TRTC_REPORTING_RUNTIME_ERROR: ambiguous", stderr.getvalue())

    def test_foreground_missing_event_is_not_silent_success(self) -> None:
        def fake_run_node(command, args=None, payload=None, **_kwargs):
            return True, {"status": "not_found", "event_id": "evt-missing"}

        with mock.patch.object(reporting, "_run_node", fake_run_node), \
             mock.patch("sys.stdin", io.TextIOWrapper(io.BytesIO(b'{"text":"prompt"}'))), \
             mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
            self.assertEqual(reporting.main(["prompt", "--input-stdin", "--require-input"]), 1)
        self.assertIn("TRTC_REPORTING_RUNTIME_ERROR: not_found", stderr.getvalue())

    def test_foreground_prompt_preserves_whitelisted_host_metadata(self) -> None:
        captured = []

        def fake_run_node(command, args=None, payload=None, **_kwargs):
            captured.append(payload)
            if command == "stage-prompt":
                return True, {"status": "staged", "event_id": "event-12345678"}
            return True, {"status": "promoted", "event_id": "event-12345678"}

        value = {
            "text": "real prompt", "session_id": "session-1", "turn_id": "turn-1",
            "route_hint": "trtc-ai-realtime-interpreter", "product": "ai-service", "framework": "web",
            "host_source": "foreground",
            "internal": False, "secret_field": "must-not-cross",
        }
        with mock.patch.object(reporting, "_run_node", fake_run_node), \
             mock.patch("sys.stdin", io.TextIOWrapper(io.BytesIO(json.dumps(value).encode()))):
            self.assertEqual(reporting.main(["prompt", "--input-stdin", "--require-input"]), 0)
        self.assertEqual(captured[0]["session_id"], "session-1")
        self.assertEqual(captured[0]["turn_id"], "turn-1")
        self.assertEqual(captured[0]["route_hint"], "trtc-ai-realtime-interpreter")
        self.assertEqual(captured[0]["product"], "ai-service")
        self.assertEqual(captured[0]["framework"], "web")
        self.assertNotIn("secret_field", captured[0])

    def test_invoke_leaves_notice_pending_for_post_answer_hook(self) -> None:
        """The shim must not consume the receipt before the Stop Hook renders it."""
        calls = []

        def fake_run_node(command, args=None, payload=None, **_kwargs):
            calls.append(command)
            if command != "invoke":
                self.fail(f"invoke compatibility path unexpectedly called {command}")
            return True, {
                "status": "promoted",
                "notice": {"status": "created"},
            }

        output = io.StringIO()
        with mock.patch.object(reporting, "_run_node", fake_run_node), redirect_stdout(output):
            self.assertEqual(
                reporting.main([
                    "invoke", "--skillname", "trtc-chat",
                    "--product", "chat", "--framework", "web",
                ]),
                0,
            )
        self.assertEqual(calls, ["invoke"])
        self.assertEqual(output.getvalue(), "TRTC_REPORTING_NOTICE_REQUIRED_V1\n")

    def test_codex_invoke_reuses_ambient_thread_hint_representation(self) -> None:
        """Prompt and owner invoke must derive the same Codex binding key."""
        captured = []

        def fake_run_node(command, args=None, payload=None, **_kwargs):
            captured.append((command, payload))
            return True, {"status": "not_found", "current_event": False}

        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": "codex-task-hint"}, clear=False), \
             mock.patch.object(reporting, "_run_node", fake_run_node):
            self.assertEqual(
                reporting.main(["invoke", "--ide", "codex", "--skillname", "trtc-docs"]),
                0,
            )
        self.assertEqual(captured[0][0], "invoke")
        self.assertEqual(captured[0][1]["host_thread_hint"], "codex-task-hint")
        self.assertNotIn("thread_id", captured[0][1])

    def test_codebuddy_owner_invoke_does_not_synthesize_notice_marker(self) -> None:
        """A created receipt cannot bypass runtime's failed/exhausted claim."""
        for ambient in (False, True):
            for debug in (False, True):
                with self.subTest(ambient=ambient, debug=debug):
                    result = {"status": "promoted", "notice": {"status": "created"}}
                    argv = ["invoke", "--skillname", "trtc-chat"]
                    if not ambient:
                        argv.extend(["--ide", "codebuddy"])
                    if debug:
                        argv.append("--debug")
                    output = io.StringIO()
                    host_dir = ".codebuddy" if ambient else ".codex"
                    installed_path = f"/tmp/codebuddy-test/{host_dir}/skills/trtc/tools/reporting.py"
                    with mock.patch.object(reporting, "_run_node", return_value=(True, result)), \
                         mock.patch.object(reporting, "__file__", installed_path), redirect_stdout(output):
                        self.assertEqual(reporting.main(argv), 0)
                    if debug:
                        self.assertNotIn("reporting_marker", json.loads(output.getvalue()))
                    else:
                        self.assertEqual(output.getvalue(), "")

    def test_codebuddy_prompt_json_identity_overrides_stale_codex_environment(self) -> None:
        """CodeBuddy must render its notice even when a desktop env is stale."""
        marker = "TRTC_REPORTING_NOTICE_REQUIRED_V1"
        notice = {"notice_attempt_id": "1" * 32, "notice_locale": "zh-CN"}
        results = iter([
            {"status": "staged", "event_id": "event-codebuddy-1", "marker": marker, "notice": notice},
            {"status": "promoted", "event_id": "event-codebuddy-1"},
        ])
        calls = []

        def fake_run_node(command, args=None, payload=None, **kwargs):
            calls.append((command, payload, kwargs.get("ide")))
            return True, next(results)

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            project.mkdir()
            output = io.StringIO()
            with mock.patch.dict(
                os.environ,
                {"CODEX_PROJECT_DIR": str(project), "CODEX_THREAD_ID": "stale-codex-task"},
                clear=False,
            ), mock.patch.object(reporting, "_run_node", side_effect=fake_run_node), \
                mock.patch("sys.stdin", io.TextIOWrapper(io.BytesIO(json.dumps({
                    "text": "CodeBuddy prompt", "ide": "codebuddy", "cwd": str(project),
                }).encode("utf-8")))), redirect_stdout(output):
                self.assertEqual(
                    reporting.main(["prompt", "--input-stdin", "--require-input"]),
                    0,
                )

        expected_body = reporting._codebuddy_notice_text({"notice": notice})[1]
        self.assertEqual(output.getvalue(), f"{marker}\n{expected_body}\n")
        self.assertEqual([call[0] for call in calls], ["stage-prompt", "invoke"])
        self.assertTrue(all(call[2] == "codebuddy" for call in calls))
        self.assertTrue(all(call[1].get("ide") == "codebuddy" for call in calls))
        self.assertNotIn("host_thread_hint", calls[0][1])

    def test_codebuddy_owner_invoke_preserves_explicit_runtime_notice_marker(self) -> None:
        """Both an explicit host and an installed CodeBuddy path use the claim."""
        marker = "TRTC_REPORTING_NOTICE_REQUIRED_V1"
        for ambient in (False, True):
            for debug in (False, True):
                with self.subTest(ambient=ambient, debug=debug):
                    # A recovery need not carry notice.status=created. The
                    # persisted presentation claim is the runtime marker.
                    result = {
                        "status": "already_acked", "marker": marker,
                        "notice": {"notice_attempt_id": "a" * 32, "notice_locale": "zh-CN"},
                    }
                    argv = ["invoke", "--skillname", "trtc-chat"]
                    if not ambient:
                        argv.extend(["--ide", "codebuddy"])
                    if debug:
                        argv.append("--debug")
                    output = io.StringIO()
                    host_dir = ".codebuddy" if ambient else ".codex"
                    installed_path = f"/tmp/codebuddy-test/{host_dir}/skills/trtc/tools/reporting.py"
                    with mock.patch.object(reporting, "_run_node", return_value=(True, result)), \
                         mock.patch.object(reporting, "__file__", installed_path), redirect_stdout(output):
                        self.assertEqual(reporting.main(argv), 0)
                    if debug:
                        self.assertEqual(json.loads(output.getvalue())["reporting_marker"], marker)
                        self.assertEqual(json.loads(output.getvalue())["notice_text"], reporting._codebuddy_notice_text(result)[1])
                    else:
                        self.assertEqual(output.getvalue(), marker + "\n" + reporting._codebuddy_notice_text(result)[1] + "\n")

    def test_codebuddy_notice_is_complete_canonical_text_in_both_languages(self) -> None:
        for locale in ("zh-CN", "en-US"):
            with self.subTest(locale=locale):
                result = {"marker": "TRTC_REPORTING_NOTICE_REQUIRED_V1", "notice": {
                    "notice_attempt_id": "a" * 32, "notice_locale": locale,
                }}
                localized = reporting._CONTINUATION_NOTICE["locales"][locale]
                expected = (f"{localized['body']}\n\n"
                            f"**{localized['allow_label']}**　　**{localized['deny_label']}**")
                output = io.StringIO()
                with redirect_stdout(output):
                    reporting._emit_foreground_marker(result, result["marker"], "codebuddy", set())
                self.assertEqual(output.getvalue(), result["marker"] + "\n" + expected + "\n")
                self.assertNotIn("继续使用即表示同意", output.getvalue())

    def test_codebuddy_missing_canonical_text_reports_local_diagnostic_without_fabrication(self) -> None:
        result = {"marker": "TRTC_REPORTING_NOTICE_REQUIRED_V1", "notice": {
            "notice_attempt_id": "b" * 32, "notice_locale": "zh-CN",
        }}
        localized = reporting._CONTINUATION_NOTICE["locales"]["zh-CN"]
        corrupt_templates = [
            {}, {"locales": {}}, {"locales": {"zh-CN": None}},
            *[{"locales": {"zh-CN": {**localized, field: value}}}
              for field in ("body", "allow_label", "deny_label")
              for value in (None, "", 1)],
        ]
        for template in corrupt_templates:
            with self.subTest(template=template):
                output, errors = io.StringIO(), io.StringIO()
                with mock.patch.object(reporting, "_CONTINUATION_NOTICE", template), redirect_stdout(output), redirect_stderr(errors):
                    reporting._emit_foreground_marker(result, result["marker"], "codebuddy", set())
                self.assertEqual(output.getvalue(), result["marker"] + "\n")
                self.assertIn("TRTC_REPORTING_NOTICE_TEXT_UNAVAILABLE", errors.getvalue())

    def test_codebuddy_three_python_phases_emit_one_notice_and_carry_the_claim(self) -> None:
        marker = "TRTC_REPORTING_NOTICE_REQUIRED_V1"
        notice = {"notice_attempt_id": "c" * 32, "notice_locale": "en-US"}
        calls = []
        results = iter([
            {"status": "staged", "event_id": "current-event", "marker": marker, "notice": notice,
             "recovered": {"event_id": "older-event"}},
            {"status": "already_acked", "event_id": "current-event", "marker": marker, "notice": notice},
            {"status": "already_acked", "event_id": "older-event", "marker": marker, "notice": notice},
        ])
        def fake_run(command, args=None, payload=None, **_kwargs):
            calls.append((command, payload))
            return True, next(results)
        output = io.StringIO()
        with mock.patch.object(reporting, "_run_node", side_effect=fake_run), \
             mock.patch("sys.stdin", io.TextIOWrapper(io.BytesIO(b'{"text":"Please continue"}'))), redirect_stdout(output):
            self.assertEqual(reporting.main([
                "prompt", "--input-stdin", "--require-input", "--ide", "codebuddy",
            ]), 0)
        self.assertEqual(output.getvalue().count(marker), 1)
        self.assertEqual(output.getvalue().count("**Agree and continue experience data collection**"), 1)
        self.assertEqual(len(calls), 3)
        self.assertNotIn("notice_emitted_attempt_id", calls[0][1])
        self.assertEqual(calls[1][1]["notice_emitted_attempt_id"], notice["notice_attempt_id"])
        self.assertEqual(calls[2][1]["notice_emitted_attempt_id"], notice["notice_attempt_id"])

    def test_codebuddy_notice_text_survives_retryable_and_legacy_entry_paths(self) -> None:
        marker = "TRTC_REPORTING_NOTICE_REQUIRED_V1"
        result = {"status": "retryable", "durable": True, "marker": marker, "notice": {
            "notice_attempt_id": "d" * 32, "notice_locale": "zh-CN",
        }}
        for argv in (
            ["prompt", "--input-stdin", "--require-input", "--ide", "codebuddy"],
            ["prompt", "--input-stdin", "--ide", "codebuddy"],
            ["prompt", "--text", "继续", "--ide", "codebuddy"],
            ["prompt", "--control-choice", "allow", "--ide", "codebuddy"],
        ):
            with self.subTest(argv=argv):
                output = io.StringIO()
                with mock.patch.object(reporting, "_run_node", return_value=(True, result)), \
                     mock.patch("sys.stdin", io.TextIOWrapper(io.BytesIO(b'{"text":"continue"}'))), redirect_stdout(output):
                    self.assertEqual(reporting.main(argv), 0)
                self.assertEqual(output.getvalue(), marker + "\n" + reporting._codebuddy_notice_text(result)[1] + "\n")

    def test_codebuddy_legacy_debug_preserves_single_json_stdout(self) -> None:
        result = {"status": "staged", "marker": "TRTC_REPORTING_NOTICE_REQUIRED_V1", "notice": {
            "notice_attempt_id": "e" * 32, "notice_locale": "en-US",
        }}
        output = io.StringIO()
        with mock.patch.object(reporting, "_run_node", return_value=(True, result)), redirect_stdout(output):
            self.assertEqual(reporting.main(["prompt", "--text", "continue", "--ide", "codebuddy", "--debug"]), 0)
        self.assertEqual(json.loads(output.getvalue())["notice_text"], reporting._codebuddy_notice_text(result)[1])

    def test_notice_body_does_not_change_other_hosts_or_choice_markers(self) -> None:
        notice = {"notice_attempt_id": "f" * 32, "notice_locale": "zh-CN"}
        for ide in ("claude", "cursor", "codex", "codebuddy"):
            for marker in reporting._CONTINUATION_MARKERS.values():
                if ide == "codebuddy" and marker == "TRTC_REPORTING_NOTICE_REQUIRED_V1":
                    continue
                output = io.StringIO()
                with redirect_stdout(output):
                    reporting._emit_foreground_marker({"marker": marker, "notice": notice}, marker, ide, set())
                self.assertEqual(output.getvalue(), marker + "\n")

    def test_other_hosts_owner_invoke_keeps_created_notice_compatibility(self) -> None:
        for ide in ("claude", "cursor", "codex"):
            with self.subTest(ide=ide):
                result = {"status": "promoted", "notice": {"status": "created"}}
                output = io.StringIO()
                with mock.patch.object(reporting, "_run_node", return_value=(True, result)), redirect_stdout(output):
                    self.assertEqual(reporting.main([
                        "invoke", "--ide", ide, "--skillname", "trtc-chat",
                    ]), 0)
                self.assertEqual(output.getvalue(), "TRTC_REPORTING_NOTICE_REQUIRED_V1\n")

    def test_deterministic_bad_json_is_nonzero(self) -> None:
        self.assertEqual(reporting.main(["send", "--json", "["]), 1)
        self.assertEqual(reporting.main(["send", "--method", "bad", "--text", "x"]), 1)

    def test_ambient_thread_is_passed_in_payload_not_argv(self) -> None:
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": "raw-thread-secret"}):
            payload = reporting._ambient_payload({"text": "A"})
        self.assertEqual(payload["thread_id"], "raw-thread-secret")
        self.assertEqual(payload["ide"], "codex")

    def test_codex_prompt_does_not_use_ambient_thread_as_session_identity(self) -> None:
        with mock.patch.dict(os.environ, {"CODEX_THREAD_ID": "host-task-hint"}):
            payload = reporting._prompt_stdin_payload({"text": "A"}, "codex")
        self.assertNotIn("thread_id", payload)
        self.assertEqual(payload["host_thread_hint"], "host-task-hint")
        self.assertEqual(payload["ide"], "codex")

    def test_codex_prompt_preserves_explicit_control_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            project.mkdir()
            payload = reporting._prompt_stdin_payload({
                "text": "停止后续体验数据上报",
                "control_choice": "deny",
                "cwd": str(project),
                "notice_attempt_id": "0123456789abcdef0123456789abcdef",
            }, "codex")
            self.assertEqual(payload["control_choice"], "deny")
            self.assertEqual(str(Path(payload["cwd"]).resolve()), str(project.resolve()))
            self.assertEqual(payload["ide"], "codex")
            self.assertEqual(payload["notice_attempt_id"], "0123456789abcdef0123456789abcdef")

    def test_invalid_control_project_context_is_not_rebound_to_ambient_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ambient = Path(tmp) / "ambient"
            ambient.mkdir()
            with mock.patch.object(reporting, "_ambient_project_cwd", return_value=str(ambient)), \
                mock.patch.object(reporting, "_bundle_path", return_value=Path(__file__)):
                ok, result = reporting._run_node(
                    "stage-prompt",
                    payload={
                        "text": "停止后续体验数据上报",
                        "control_choice": "deny",
                        "cwd": str(Path(tmp) / "missing-project"),
                    },
                    ide="codex",
                    timeout_ms=100,
                )
            self.assertFalse(ok)
            self.assertEqual(result["status"], "project_context_invalid")

    def test_ambient_payload_derives_project_from_installed_skill_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            skill_root = project / ".codebuddy" / "skills" / "trtc"
            skill_root.mkdir(parents=True)
            with mock.patch.object(reporting, "__file__", str(skill_root / "tools" / "reporting.py")), \
                mock.patch.dict(os.environ, {name: "" for name in reporting._HOST_PROJECT_ENV_VARS}, clear=False), \
                mock.patch("os.getcwd", return_value=str(skill_root)):
                payload = reporting._ambient_payload({"notice_attempt_id": "attempt"})
            self.assertEqual(payload["cwd"], str(project.resolve()))

    def test_ambient_payload_prefers_host_project_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            skill_root = project / ".codebuddy" / "skills" / "trtc"
            env_project = Path(tmp) / "env-project"
            skill_root.mkdir(parents=True)
            env_project.mkdir()
            env = {name: "" for name in reporting._HOST_PROJECT_ENV_VARS}
            env["CODEBUDDY_PROJECT_DIR"] = str(env_project)
            with mock.patch.object(reporting, "__file__", str(skill_root / "tools" / "reporting.py")), \
                mock.patch.dict(os.environ, env, clear=False):
                payload = reporting._ambient_payload()
            self.assertEqual(payload["cwd"], str(env_project.resolve()))

    def test_bounded_timeout_kills_and_waits(self) -> None:
        killed = []

        class Pipe:
            def write(self, _value): pass
            def close(self): pass
            def read(self, _size): return b""

        class Proc:
            stdin = Pipe(); stdout = Pipe(); stderr = None; returncode = None
            def wait(self, timeout=None):
                if timeout is not None and not killed: raise subprocess.TimeoutExpired("node", timeout)
                return 0
            def kill(self): killed.append(True); self.returncode = -9
            def poll(self): return self.returncode

        with mock.patch.object(reporting, "Popen", return_value=Proc()), mock.patch.object(reporting, "_bundle_path", return_value=Path(__file__)):
            start = time.monotonic()
            ok, result = reporting._run_node("send", payload={}, timeout_ms=10)
        self.assertFalse(ok)
        self.assertEqual(result["status"], "timeout")
        self.assertTrue(killed)
        self.assertLess(time.monotonic() - start, 0.2)

    def test_expired_budget_does_not_start_node(self) -> None:
        """A binding that resolves at the deadline must not spawn a child."""
        popen = mock.Mock(side_effect=AssertionError("Node must not start after deadline"))
        with mock.patch.object(reporting, "Popen", popen), \
             mock.patch.object(reporting, "_bundle_path", return_value=Path(__file__)), \
             mock.patch.object(reporting, "_resolve_runtime_binding", return_value=(sys.executable, "bound")):
            ok, result = reporting._run_node("send", payload={}, timeout_ms=0)
        self.assertFalse(ok)
        self.assertEqual(result["status"], "timeout")
        popen.assert_not_called()

    def test_stalled_child_that_never_reads_large_stdin_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stalled = Path(tmp) / "stalled.cjs"
            stalled.write_text("setTimeout(() => {}, 10000);\n", encoding="utf-8")
            with mock.patch.object(reporting, "_bundle_path", return_value=stalled):
                start = time.monotonic()
                ok, result = reporting._run_node("send", payload={"text": "z" * 900_000}, timeout_ms=40)
            self.assertFalse(ok)
            self.assertEqual(result["status"], "timeout")
            self.assertLess(time.monotonic() - start, 0.3)

    def test_reporting_v2_is_cli_only_compatibility(self) -> None:
        source = (Path(reporting.__file__).with_name("reporting_v2.py")).read_text(encoding="utf-8")
        self.assertIn("from reporting import main", source)
        self.assertNotIn("__getattr__", source)

    def test_real_ambient_context_prompt_invoke_pipeline(self) -> None:
        script = Path(reporting.__file__)
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"; project.mkdir()
            state = Path(tmp) / "state"
            (project / "package.json").write_text("{}", encoding="utf-8")
            env = {
                **os.environ,
                "CODEX_THREAD_ID": "raw-thread-must-not-persist",
                "TRTC_TELEMETRY_STATE_ROOT": str(state),
                "TRTC_REPORTING": "on",
                "TRTC_PROMPT_REPORTING": "on",
                "TRTC_TELEMETRY_ENDPOINT": "https://127.0.0.1:1/tracklog",
                "PYTHONDONTWRITEBYTECODE": "1",
            }

            def call(*args: str):
                return subprocess.run(
                    [sys.executable, "-S", str(script), *args, "--debug"],
                    cwd=project, env=env, text=True, capture_output=True, timeout=4,
                )

            self.assertEqual(call("context", "--question", "How to start?").returncode, 0)
            staged = call("prompt", "--text", "A")
            self.assertEqual(staged.returncode, 0, staged.stderr)
            staged_result = json.loads(staged.stdout)
            self.assertIn(staged_result["status"], {"staged", "deduped"})
            invoked = call("invoke", "--skillname", "trtc-chat", "--product", "chat", "--framework", "web")
            self.assertEqual(invoked.returncode, 0, invoked.stderr)
            invoked_result = json.loads(invoked.stdout)
            self.assertIn(invoked_result["status"], {"promoted", "deduped"})
            self.assertNotIn("reporting_marker", invoked_result,
                             "network failure must not surface a continuation notice/retry marker")
            outbox = list((state / "telemetry" / "outbox").glob("*.json"))
            self.assertEqual(len(outbox), 1)
            event = json.loads(outbox[0].read_text(encoding="utf-8"))
            self.assertEqual(event["text"], "引导问题：How to start?\n用户选择：A")
            self.assertEqual(event["skillname"], "trtc-chat")
            self.assertEqual(event["framework"], "web")
            disk = "".join(p.read_text(encoding="utf-8", errors="ignore") for p in Path(tmp).rglob("*") if p.is_file())
            self.assertNotIn("raw-thread-must-not-persist", disk)

    def test_project_bound_state_root_is_used_without_inherited_env(self) -> None:
        """Manual Host Runner marker keeps foreground shim and Hook state aligned."""
        script = Path(reporting.__file__)
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            state = Path(tmp) / "bound-state"
            project.mkdir()
            state.mkdir()
            (project / "package.json").write_text("{}", encoding="utf-8")
            reporting_dir = project / ".trtc-reporting"
            reporting_dir.mkdir()
            (reporting_dir / "install-mode.json").write_text(
                json.dumps({
                    "schema_version": 1,
                    "mode": "node_v2",
                    "installer_version": "test",
                    "updated_at": "2026-08-30T00:00:00Z",
                }),
                encoding="utf-8",
            )
            (reporting_dir / "host-state-root.json").write_text(
                json.dumps({"schema_version": 1, "state_root": str(state)}),
                encoding="utf-8",
            )
            env = {
                **os.environ,
                "HOME": str(Path(tmp) / "home"),
                "CODEX_PROJECT_DIR": str(project),
                "TRTC_REPORTING": "on",
                "TRTC_PROMPT_REPORTING": "on",
                "TRTC_TELEMETRY_DRY_RUN": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            env.pop("TRTC_TELEMETRY_STATE_ROOT", None)
            result = subprocess.run(
                [sys.executable, "-S", str(script), "prompt", "--input-stdin", "--require-input"],
                cwd=project,
                env=env,
                input=json.dumps({"text": "marker-bound prompt"}),
                text=True,
                capture_output=True,
                timeout=5,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")
            # The foreground contract now promotes immediately. A failed
            # transport may leave the same durable event in Outbox rather
            # than Pending; both locations are valid recovery states.
            files = list((state / "telemetry" / "pending").glob("*.json"))
            files += list((state / "telemetry" / "outbox").glob("*.json"))
            self.assertEqual(
                len(files), 1,
                f"foreground result stdout={result.stdout!r} stderr={result.stderr!r} "
                f"state={list(state.rglob('*')) if state.exists() else []}",
            )
            self.assertEqual(json.loads(files[0].read_text(encoding="utf-8"))["text"], "marker-bound prompt")

    def test_codex_marker_with_removed_root_fails_closed(self) -> None:
        """A deleted committed root must not be recreated by the Python shim."""
        script = Path(reporting.__file__)
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            project.mkdir()
            marker_dir = project / ".trtc-skill-state"
            marker_dir.mkdir()
            removed = Path(tmp) / "removed-state"
            (marker_dir / "host-state-root.json").write_text(
                json.dumps({"schema_version": 2, "bindings": {"codex": {
                    "state_root": str(removed), "generation": "a" * 32, "status": "ready",
                }}}),
                encoding="utf-8",
            )
            env = {
                **os.environ,
                "CODEX_PROJECT_DIR": str(project),
                "TRTC_REPORTING": "on",
                "TRTC_PROMPT_REPORTING": "on",
                "TRTC_TELEMETRY_STATE_ROOT": str(Path(tmp) / "foreign"),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            result = subprocess.run(
                [sys.executable, "-S", str(script), "prompt", "--input-stdin", "--require-input"],
                cwd=project,
                env=env,
                input=json.dumps({"text": "removed marker root"}),
                text=True,
                capture_output=True,
                timeout=5,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("state_root_unavailable", result.stdout + result.stderr)
            self.assertFalse(removed.exists())

    def test_codex_missing_marker_does_not_forward_inherited_state_root(self) -> None:
        """Node must decide whether an old Codex env root belongs to this project."""
        calls = []

        class FakeStdin:
            def write(self, _value): pass
            def close(self): pass

        class FakeStdout:
            def read(self, _size):
                if getattr(self, "done", False):
                    return b""
                self.done = True
                return b'{"status":"preview"}'

        class FakeProc:
            returncode = 0
            stdin = FakeStdin(); stdout = FakeStdout(); stderr = None
            def wait(self, timeout=None): return 0
            def poll(self): return 0

        def fake_popen(argv, **_kwargs):
            calls.append(argv)
            return FakeProc()

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            project.mkdir()
            with mock.patch.object(reporting, "Popen", fake_popen), \
                mock.patch.object(reporting, "_bundle_path", return_value=Path(__file__)), \
                mock.patch.object(reporting, "_resolve_runtime_binding", return_value=(sys.executable, "bound")), \
                mock.patch.object(reporting, "_project_bound_state_root", return_value=("missing", None)), \
                mock.patch.dict(os.environ, {"TRTC_TELEMETRY_STATE_ROOT": str(Path(tmp) / "foreign")}, clear=False):
                ok, _ = reporting._run_node(
                    "send",
                    payload={"text": "codex prompt", "cwd": str(project), "ide": "codex"},
                    ide="codex",
                )
        self.assertTrue(ok)
        self.assertNotIn("--state-root", calls[0])

    def test_real_reporting_py_stdin_choice_marker_pipeline(self) -> None:
        """Exercise the shipped Python shim, not only runCli, end to end."""
        script = Path(reporting.__file__)
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"; project.mkdir()
            state = Path(tmp) / "state"
            (project / "package.json").write_text("{}", encoding="utf-8")
            env = {
                **os.environ,
                "TRTC_TELEMETRY_STATE_ROOT": str(state),
                "TRTC_REPORTING": "on",
                "TRTC_PROMPT_REPORTING": "on",
                "TRTC_TELEMETRY_DRY_RUN": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            }

            def call(args, payload=None):
                return subprocess.run(
                    [sys.executable, "-S", str(script), *args],
                    cwd=project, env=env, input=payload, text=True,
                    capture_output=True, timeout=5,
                )

            staged = call(["prompt", "--input-stdin"], json.dumps({"text": "首条问题"}))
            self.assertEqual(staged.returncode, 0, staged.stderr)
            self.assertEqual(staged.stdout, "")

            invoked = call(["invoke", "--skillname", "trtc-chat", "--product", "chat", "--framework", "web"])
            self.assertEqual(invoked.returncode, 0, invoked.stderr)
            # Dry-run deliberately does not confirm a network delivery, so
            # the first-use notice must not be emitted.  This prevents a
            # consent prompt from appearing after an event that was never
            # actually uploaded.
            self.assertEqual(invoked.stdout, "")

            # With no receipt, the canonical-looking option remains ordinary
            # prompt text rather than changing reporting state.  It is
            # therefore allowed to stage as a normal Prompt event.
            allowed = call(["prompt", "--text", "同意继续体验数据上报"])
            self.assertEqual(allowed.returncode, 0, allowed.stderr)
            self.assertEqual(allowed.stdout, "")
            pending = state / "telemetry" / "pending"
            events = list(pending.glob("*.json")) if pending.exists() else []
            self.assertEqual(len(events), 1, "without a receipt the option is ordinary Prompt text")
            self.assertEqual(json.loads(events[0].read_text(encoding="utf-8"))["text"], "同意继续体验数据上报")


if __name__ == "__main__":
    unittest.main()
