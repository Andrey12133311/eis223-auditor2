"""Exercise private credential retries without running privileged installation."""
import ast
import contextlib
import getpass
import io
from pathlib import Path
import unittest
from unittest.mock import patch

from scripts import eis_vps_worker as worker


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'install_eis_vps_worker.sh'
SOURCE = SCRIPT.read_text(encoding='utf-8').split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
FUNCTION = next(node for node in ast.parse(SOURCE).body
                if isinstance(node, ast.FunctionDef) and node.name == 'read_secret')
NAMESPACE = {'getpass': getpass}
exec(compile(ast.Module(body=[FUNCTION], type_ignores=[]), str(SCRIPT), 'exec'), NAMESPACE)
read_secret = NAMESPACE['read_secret']
INTERVAL_FUNCTION = next(node for node in ast.parse(SOURCE).body
                         if isinstance(node, ast.FunctionDef) and node.name == 'configure_intervals')
exec(compile(ast.Module(body=[INTERVAL_FUNCTION], type_ignores=[]), str(SCRIPT), 'exec'), NAMESPACE)
configure_intervals = NAMESPACE['configure_intervals']


class InstallerIntervalTests(unittest.TestCase):
    def test_upgrade_preserves_keys_and_enables_faster_search(self):
        config = {'site_token': 'fixture-site', 'eis_token': 'fixture-eis', 'interval_seconds': 300}
        configure_intervals(config)
        self.assertEqual((config['interval_seconds'], config['search_interval_seconds']), (30, 60))
        self.assertEqual((config['site_token'], config['eis_token']), ('fixture-site', 'fixture-eis'))

    def test_custom_intervals_survive_repeat_install(self):
        config = {'interval_seconds': 90, 'interval_custom': True, 'search_interval_seconds': 120}
        configure_intervals(config)
        self.assertEqual((config['interval_seconds'], config['search_interval_seconds']), (90, 120))

    def test_explicit_search_override_does_not_change_queue(self):
        config = {'interval_seconds': 90, 'interval_custom': True, 'search_interval_seconds': 300}
        configure_intervals(config, search_interval_seconds=30)
        self.assertEqual((config['interval_seconds'], config['search_interval_seconds']), (90, 30))

    def test_invalid_saved_search_interval_is_rejected(self):
        for value in (0, 29, 3601, True, '60'):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                configure_intervals({'search_interval_seconds': value})


class InstallerCredentialTests(unittest.TestCase):
    def test_only_malformed_field_is_reprompted_and_secret_is_never_printed(self):
        output = io.StringIO()
        bad_secret = 'fixture-private-секрет'
        with patch.object(getpass, 'getpass', return_value='replacement-site-value') as prompt:
            with contextlib.redirect_stdout(output):
                eis = read_secret(worker, 'eis_token', 'Токен ЕИС', 'saved-eis-value')
                site = read_secret(worker, 'site_token', 'Ключ сайта', bad_secret)
        self.assertEqual(eis, 'saved-eis-value')
        self.assertEqual(site, 'replacement-site-value')
        prompt.assert_called_once_with('Ключ сайта (ввод скрыт): ')
        self.assertIn('недопустимые символы', output.getvalue())
        for secret in (bad_secret, eis, site):
            self.assertNotIn(secret, output.getvalue())

    def test_empty_and_variable_name_retry_until_value_is_entered(self):
        output = io.StringIO()
        with patch.object(getpass, 'getpass', side_effect=['', 'EIS223_UPLOAD_TOKEN', 'opaque-key']) as prompt:
            with contextlib.redirect_stdout(output):
                value = read_secret(worker, 'site_token', 'Ключ сайта')
        self.assertEqual(value, 'opaque-key')
        self.assertEqual(prompt.call_count, 3)
        self.assertIn('пустой ввод', output.getvalue())
        self.assertIn('Скопируй её ЗНАЧЕНИЕ', output.getvalue())
        self.assertNotIn(value, output.getvalue())

    def test_valid_saved_credentials_need_no_prompt(self):
        with patch.object(getpass, 'getpass') as prompt:
            self.assertEqual(read_secret(worker, 'site_token', 'Ключ сайта', 'saved-key'), 'saved-key')
        prompt.assert_not_called()

    def test_cancelled_private_input_propagates_without_echo(self):
        output = io.StringIO()
        with patch.object(getpass, 'getpass', side_effect=KeyboardInterrupt):
            with contextlib.redirect_stdout(output), self.assertRaises(KeyboardInterrupt):
                read_secret(worker, 'site_token', 'Ключ сайта')
        self.assertEqual(output.getvalue(), '')


if __name__ == '__main__':
    unittest.main()
