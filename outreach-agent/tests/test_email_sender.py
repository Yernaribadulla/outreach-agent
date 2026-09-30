from __future__ import annotations

import smtplib
import traceback
import unittest
from unittest.mock import MagicMock, patch

from app.email.sender import SMTPProvider, SendError, SimulatedProvider, smtp_configuration_ready


SECRET = "smtp-password-sentinel-do-not-leak"


def smtp_env(**overrides: str) -> dict[str, str]:
    env = {
        "SIMULATED_SEND": "false",
        "SMTP_ENABLED": "false",  # explicit SIMULATED_SEND takes precedence
        "SMTP_HOST": "smtp.example.test",
        "SMTP_PORT": "465",
        "SMTP_USERNAME": "sender@example.test",
        "SMTP_PASSWORD": SECRET,
        "SMTP_FROM": "Sender <sender@example.test>",
    }
    env.update(overrides)
    return env


class SMTPConfigurationTests(unittest.TestCase):
    def test_simulated_flag_always_forbids_smtp(self):
        self.assertFalse(smtp_configuration_ready(smtp_env(SIMULATED_SEND="true", SMTP_ENABLED="true")))

    def test_explicit_real_flag_overrides_legacy_disabled_flag(self):
        self.assertTrue(smtp_configuration_ready(smtp_env(SIMULATED_SEND="false", SMTP_ENABLED="false")))

    def test_legacy_enabled_setting_is_supported_when_new_flag_is_absent(self):
        env = smtp_env(SMTP_ENABLED="true")
        env.pop("SIMULATED_SEND")
        self.assertTrue(smtp_configuration_ready(env))

    def test_missing_or_invalid_smtp_values_fail_closed(self):
        self.assertFalse(smtp_configuration_ready(smtp_env(SMTP_PASSWORD="")))
        self.assertFalse(smtp_configuration_ready(smtp_env(SMTP_PORT="not-a-port")))
        self.assertFalse(smtp_configuration_ready(smtp_env(SMTP_FROM="not-an-email")))
        self.assertFalse(smtp_configuration_ready(smtp_env(SMTP_USE_SSL="perhaps")))
        self.assertFalse(smtp_configuration_ready(smtp_env(SMTP_PORT="587", SMTP_USE_TLS="false")))


class SMTPProviderTests(unittest.TestCase):
    def setUp(self):
        self.server = MagicMock()
        self.server.__enter__.return_value = self.server
        self.server.__exit__.return_value = False
        self.smtp_ssl = patch("app.email.sender.smtplib.SMTP_SSL", return_value=self.server)
        self.smtp_ssl.start()
        self.addCleanup(self.smtp_ssl.stop)

    def test_check_available_authenticates_without_sending(self):
        SMTPProvider(smtp_env()).check_available()
        self.server.login.assert_called_once_with("sender@example.test", SECRET)
        self.server.noop.assert_called_once()
        self.server.send_message.assert_not_called()

    def test_send_success_uses_mock_smtp_and_html_alternative(self):
        SMTPProvider(smtp_env()).send("clinic@example.test", "Subject", "Plain body", "<p>HTML body</p>")
        self.server.send_message.assert_called_once()
        message = self.server.send_message.call_args.args[0]
        self.assertEqual(message["To"], "clinic@example.test")
        self.assertEqual(message["Subject"], "Subject")
        self.assertEqual(len(message.get_payload()), 2)

    def test_authentication_failure_is_safe(self):
        self.server.login.side_effect = smtplib.SMTPAuthenticationError(535, f"rejected {SECRET}".encode())
        with self.assertRaises(SendError) as caught:
            SMTPProvider(smtp_env()).check_available()
        self.assertEqual(caught.exception.category, "AUTH")
        self.assertNotIn(SECRET, str(caught.exception))
        self.assertNotIn(SECRET, "".join(traceback.format_exception(caught.exception)))

    def test_connection_failure_is_safe(self):
        with patch("app.email.sender.smtplib.SMTP_SSL", side_effect=OSError(f"connect failed {SECRET}")):
            with self.assertRaises(SendError) as caught:
                SMTPProvider(smtp_env()).check_available()
        self.assertEqual(caught.exception.category, "CONNECTION")
        self.assertNotIn(SECRET, str(caught.exception))
        self.assertNotIn(SECRET, "".join(traceback.format_exception(caught.exception)))

    def test_simulated_mode_refuses_direct_smtp_provider(self):
        with patch("app.email.sender.smtplib.SMTP_SSL") as connection:
            with self.assertRaises(SendError):
                SMTPProvider(smtp_env(SIMULATED_SEND="true")).send("clinic@example.test", "S", "B")
        connection.assert_not_called()
        provider = SimulatedProvider()
        provider.send("clinic@example.test", "S", "B")
        self.assertEqual(len(provider.sent), 1)


if __name__ == "__main__":
    unittest.main()
