"""Focused local-browser coverage for profile load/retry/save feedback.

EIS_PROFILE_BROWSER_TESTS=1 PYTHONPATH=.:scripts:tests python -m unittest test_v371_profile_ui
All routes are fulfilled locally; no production requests are sent.
"""
import json
import os
import unittest

from scripts.v371_profile_ui import render_profile


@unittest.skipUnless(os.environ.get("EIS_PROFILE_BROWSER_TESTS") == "1", "Optional sandboxed Chromium checks")
class ProfileBrowserTests(unittest.TestCase):
    def test_failed_load_retry_and_save_keep_fields_safe_and_feedback_accessible(self):
        from playwright.sync_api import expect, sync_playwright
        origin="https://profile.eis.test"
        person={"last_name":"Иванов","first_name":"Александр","patronymic":"Сергеевич",
                "full_name":"Иванов Александр Сергеевич","email":"preview@example.test"}
        gets=[];puts=[];pending=[];unexpected=[]

        def handler(route):
            request=route.request
            if request.url==origin+"/account/profile":
                route.fulfill(status=200,content_type="text/html",body=render_profile())
            elif request.url==origin+"/me/profile" and request.method=="GET":
                gets.append(request)
                route.fulfill(status=503 if len(gets)==1 else 200,content_type="application/json",
                              body=json.dumps({} if len(gets)==1 else person))
            elif request.url==origin+"/me/profile" and request.method=="PUT":
                puts.append(request.post_data_json);pending.append(route)
            else:
                unexpected.append(request.url);route.abort()

        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(executable_path="/usr/bin/chromium",chromium_sandbox=True)
            try:
                page=browser.new_page(viewport={"width":390,"height":844},has_touch=True,is_mobile=True)
                page.route("**/*",handler)
                page.goto(origin+"/account/profile",wait_until="domcontentloaded")
                status=page.locator("#profile-status")
                expect(status).to_have_attribute("data-state","error")
                expect(page.locator("input[name=last_name]")).to_be_disabled()
                expect(page.locator("button[type=submit]")).to_be_disabled()
                retry=page.get_by_role("button",name="Повторить загрузку")
                expect(retry).to_be_visible();retry.click()
                expect(page.locator("input[name=last_name]")).to_be_enabled()
                expect(page.locator("input[name=last_name]")).to_have_value("Иванов")
                expect(retry).to_be_hidden()
                self.assertEqual(page.locator(".profile-back").get_attribute("href"),"/#cabinet")
                self.assertEqual(status.get_attribute("aria-live"),"polite")
                page.locator("input[name=last_name]").fill("Петров")
                page.get_by_role("button",name="Сохранить изменения").click()
                expect(page.locator("#profile-edit")).to_have_attribute("aria-busy","true")
                expect(page.locator("input[name=last_name]")).to_be_disabled()
                expect(page.get_by_role("button",name="Сохраняем…")).to_be_disabled()
                self.assertEqual(puts,[{"last_name":"Петров","first_name":"Александр","patronymic":"Сергеевич"}])
                pending.pop().fulfill(status=422,content_type="application/json",body=json.dumps({"detail":"Проверьте фамилию"}))
                expect(status).to_have_text("Проверьте фамилию")
                expect(page.locator("input[name=last_name]")).to_be_enabled()
                expect(page.locator("input[name=last_name]")).to_have_value("Петров")
                expect(page.locator("button[type=submit]")).to_be_focused()
                page.get_by_role("button",name="Сохранить изменения").click()
                expect(page.locator("#profile-edit")).to_have_attribute("aria-busy","true")
                pending.pop().fulfill(status=200,content_type="application/json",
                                      body=json.dumps({"full_name":"Петров Александр Сергеевич"}))
                expect(status).to_have_attribute("data-state","saved")
                expect(page.locator("#profile-name")).to_have_text("Петров Александр Сергеевич")
                expect(page.locator("button[type=submit]")).to_be_enabled()
                expect(page.locator("button[type=submit]")).to_be_focused()
                self.assertEqual(len(puts),2)
                self.assertEqual(unexpected,[])
                self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"),390)
                for control in page.locator("input,button[type=submit],.profile-back").all():
                    self.assertGreaterEqual(control.bounding_box()["height"],44)
                page.locator("input[name=first_name]").focus()
                self.assertNotEqual(page.locator("input[name=first_name]").evaluate("e=>getComputedStyle(e).outlineStyle"),"none")
            finally:
                browser.close()


if __name__=="__main__":
    unittest.main()
