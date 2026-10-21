"""Contract tests with synthetic responses. No real key or network access."""
import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import collector
import supadata_adapter as adapter


class HostedProvider(unittest.TestCase):
    def client(self, status=200, payload=None):
        session = Mock()
        response = Mock(status_code=status)
        response.json.return_value = payload or {
            "lang": "en", "content": [{"text": "Synthetic provider sentence.", "offset": 1250, "duration": 2500}]}
        session.get.return_value = response
        return adapter.Client(key="synthetic-test-key", session=session), session

    def test_native_only_request_preserves_timing_and_source(self):
        client, session = self.client()
        result = client.fetch("demo0000000")
        self.assertEqual(result["segments"][0]["start"], 1.25)
        self.assertEqual(result["segments"][0]["duration"], 2.5)
        self.assertEqual(result["method"], "supadata_native_captions")
        _, kwargs = session.get.call_args
        self.assertEqual(kwargs["params"]["mode"], "native")
        self.assertEqual(kwargs["params"]["text"], "false")
        self.assertFalse(kwargs["allow_redirects"])
        self.assertNotIn("synthetic-test-key", json.dumps(result))

    def test_credit_limit_stops_and_does_not_retry(self):
        client, session = self.client(status=402)
        with self.assertRaises(adapter.ProviderStopped) as caught:
            client.fetch("demo0000000")
        self.assertTrue(collector.classify_error(caught.exception)["stop_run"])
        session.get.assert_called_once()

    def test_credit_balance_is_checked_without_a_caption_request(self):
        client, session = self.client(payload={"plan": "Free (100/mo)", "maxCredits": 100, "usedCredits": 3})
        self.assertEqual(client.allowance()["remaining"], 97)
        args, kwargs = session.get.call_args
        self.assertEqual(args[0], "https://api.supadata.ai/v1/me")
        self.assertIsNone(kwargs["params"])

    def test_unknown_credit_balance_fails_before_collection(self):
        client, _ = self.client(payload={"plan": "Free"})
        with self.assertRaises(ValueError):
            client.allowance()

    def test_missing_captions_does_not_fall_back_to_generation(self):
        client, session = self.client(status=206)
        with self.assertRaises(ValueError):
            client.fetch("demo0000000")
        session.get.assert_called_once()

    def test_non_english_response_is_not_silently_accepted(self):
        client, _ = self.client(payload={"lang": "es", "content": [{"text": "Synthetic", "offset": 0, "duration": 1000}]})
        with self.assertRaises(ValueError):
            client.fetch("demo0000000")

    def test_key_setup_keeps_secret_local_with_private_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            key_path = Path(tmp) / "key"
            with patch.object(adapter, "KEY_PATH", key_path), \
                 patch.object(adapter.getpass, "getpass", return_value="synthetic-test-key"), \
                 patch("builtins.print") as output:
                adapter.configure_key()
                self.assertEqual(key_path.read_text().strip(), "synthetic-test-key")
                self.assertEqual(stat.S_IMODE(key_path.stat().st_mode), 0o600)
                self.assertNotIn("synthetic-test-key", str(output.call_args))

    def test_hosted_resume_caps_new_requests_and_keeps_previous_success(self):
        import contextlib
        import io
        from test_collector import source, captions
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "run"
            with patch.object(sys, "argv", ["collector.py", "--output", str(root), "--pilot", "1"]), \
                 patch.object(collector, "inventory", return_value=source()), \
                 patch.object(collector, "make_api", return_value=object()), \
                 patch.object(collector, "fetch_english", side_effect=captions), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(collector.main(), 0)
            prior = (root / "timestamped/demo0000000.json").read_bytes()
            client = Mock()
            client.fetch.side_effect = captions
            with patch.object(sys, "argv", ["collector.py", "--resume", str(root), "--all", "--provider", "supadata"]), \
                 patch.object(adapter, "Client", return_value=client), \
                 patch.object(collector.time, "sleep"), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(collector.main(), 0)
            self.assertEqual(client.fetch.call_count, 3)
            self.assertEqual([c.args[0] for c in client.fetch.call_args_list],
                             ["demo0000001", "demo0000002", "demo0000003"])
            self.assertEqual((root / "timestamped/demo0000000.json").read_bytes(), prior)
            summary = json.loads((root / "summary.json").read_text())
            self.assertEqual((summary["successful"], summary["gaps"]), (4, 1))

    def test_replacing_configured_key_updates_only_the_secret_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            key_path = root / "key"
            collection = root / "source.json"
            collection.write_text('{"synthetic": "unchanged"}')
            with patch.object(adapter, "KEY_PATH", key_path), \
                 patch.object(adapter.getpass, "getpass", side_effect=["synthetic-old-key", "synthetic-new-key"]), \
                 patch("builtins.print"):
                adapter.configure_key()
                adapter.configure_key()
            self.assertEqual(key_path.read_text().strip(), "synthetic-new-key")
            self.assertEqual(stat.S_IMODE(key_path.stat().st_mode), 0o600)
            self.assertEqual(collection.read_text(), '{"synthetic": "unchanged"}')

    def test_available_balance_caps_resume_below_remaining_inventory(self):
        import contextlib
        import io
        from test_collector import source, captions
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "run"
            with patch.object(sys, "argv", ["collector.py", "--output", str(root), "--pilot", "1"]), \
                 patch.object(collector, "inventory", return_value=source()), \
                 patch.object(collector, "make_api", return_value=object()), \
                 patch.object(collector, "fetch_english", side_effect=captions), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(collector.main(), 0)
            client = Mock()
            client.allowance.return_value = {"remaining": 2}
            client.fetch.side_effect = captions
            with patch.object(sys, "argv", ["collector.py", "--resume", str(root), "--all", "--provider", "supadata", "--use-available-credits"]), \
                 patch.object(adapter, "Client", return_value=client), \
                 patch.object(collector.time, "sleep"), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(collector.main(), 0)
            client.allowance.assert_called_once()
            self.assertEqual(client.fetch.call_count, 2)
            summary = json.loads((root / "summary.json").read_text())
            self.assertEqual((summary["successful"], summary["gaps"]), (3, 2))


if __name__ == "__main__":
    unittest.main()
