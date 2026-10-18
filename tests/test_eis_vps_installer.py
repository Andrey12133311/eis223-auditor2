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
