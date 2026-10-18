"""Exercise the dashboard reset click with real DOM event propagation."""
import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import v303_dashboard_fixes as dashboard


DOM_RUNNER = r"""
const {JSDOM, VirtualConsole} = require('jsdom');
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const errors = [];
const console = new VirtualConsole();
console.on('jsdomError', error => errors.push(error.message));
const dom = new JSDOM(`<!doctype html><main>
<form id="filters">
 <input id="query" name="q" value="Насосы">
 <input id="inn" name="inn" value="7700000000">
 <input id="min" name="min_price" type="number" value="1000">
 <input id="max" name="max_price" type="number" value="5000">
 <input id="published" name="published" type="date" value="2026-10-01">
 <select id="kind" name="result_kind"><option value="">Все</option><option value="violation" selected>Нарушения</option></select>
 <select id="method" name="method"><option value="">Все способы</option><option value="auction" selected>Аукцион</option></select>
 <select id="v257-violation-type" name="violation_type"><option value="">Все нарушения</option><option value="late" selected>Сроки</option></select>
 <select id="regions" name="region" multiple><option value="77" selected>Москва</option><option value="78" selected>Санкт-Петербург</option></select>
 <details id="forms"><summary>Формы</summary><label><input name="org_form" type="checkbox" value="АО" checked>АО</label><label><input name="org_form" type="checkbox" value="ООО" checked>ООО</label></details>
 <input name="competitive" type="radio" value="1" checked>
 <textarea name="notes">Сохранённый фильтр</textarea>
 <input name="csrf" type="hidden" value="retained-token">
 <button id="reset" type="button"><span>Сбросить фильтры</span></button>
</form><p id="message" class="error">Ошибка</p>
<p id="customer-progress-v233">Уточняется</p></main>`, {
 url: 'https://site.example/', runScripts: 'outside-only',
 pretendToBeVisual: true, virtualConsole: console,
});
const w = dom.window, form = w.document.getElementById('filters');
const requests = [];
let obsoleteClicks = 0;
// jsdom omits named form-property shadowing present in real browsers.
form.reset = w.document.getElementById('reset');
form.querySelector('#reset').addEventListener('click', () => obsoleteClicks++);
form.addEventListener('submit', event => {
 event.preventDefault();
 const params = new w.URLSearchParams();
 for (const [key,value] of new w.FormData(form)) {
  if (key !== 'csrf' && value) params.append(key, value);
 }
 requests.push({method: 'GET', url: '/api/v257/dashboard?' + params});
});
w.eval(input.script);
// Click the text span to exercise closest('#reset') and capture listeners.
form.querySelector('#reset span').click();
const fields = Array.from(form.querySelectorAll('input,select,textarea')).map(field => ({
 name: field.name, type: field.type, value: field.value,
 checked: field.checked || false,
 selected: field.tagName === 'SELECT' && field.multiple ? Array.from(field.selectedOptions).map(x=>x.value) : null,
}));
const result = {errors, requests, fields, obsoleteClicks,
 message: w.document.getElementById('message').textContent,
 messageError: w.document.getElementById('message').classList.contains('error')};
dom.window.close();
process.stdout.write(JSON.stringify(result));
"""


class DashboardFilterResetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("node"):
            raise unittest.SkipTest("Node is required for the DOM regression")
        probe = subprocess.run(["node", "-e", "require('jsdom')"],
                               capture_output=True, timeout=15)
        if probe.returncode:
            raise unittest.SkipTest("jsdom is required for the DOM regression")

    def run_dom(self, script):
        result = subprocess.run(["node", "-e", DOM_RUNNER],
                                input=json.dumps({"script": script}), text=True,
                                capture_output=True, timeout=20, env=os.environ.copy())
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_named_reset_button_reproduces_previous_runtime_failure(self):
        script = re.sub(r"^<script[^>]*>|</script>$", "", dashboard.SCRIPT.strip())
        broken = script.replace("HTMLFormElement.prototype.reset.call(form);", "form.reset();")
        result = self.run_dom(broken)
        self.assertTrue(any("form.reset is not a function" in error for error in result["errors"]), result)
        self.assertEqual(result["requests"], [])
        self.assertEqual(result["obsoleteClicks"], 0)

    def test_reset_clears_all_visible_filters_before_one_dashboard_refresh(self):
        script = re.sub(r"^<script[^>]*>|</script>$", "", dashboard.SCRIPT.strip())
        result = self.run_dom(script)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["requests"], [{"method": "GET", "url": "/api/v257/dashboard?"}])
        self.assertEqual(result["obsoleteClicks"], 0)
        self.assertEqual(result["message"], "")
        self.assertFalse(result["messageError"])
        for field in result["fields"]:
            with self.subTest(field=field["name"]):
                if field["type"] in ("checkbox", "radio"):
                    self.assertFalse(field["checked"])
                elif field["type"] == "hidden":
                    self.assertEqual(field["value"], "retained-token")
                else:
                    self.assertEqual(field["value"], "")
                if field["selected"] is not None:
                    self.assertEqual(field["selected"], [])


if __name__ == "__main__":
    unittest.main()
