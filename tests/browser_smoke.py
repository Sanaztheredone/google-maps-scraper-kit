"""Browser smoke test with a fake engine; never starts a live scrape."""
import os
from pathlib import Path
import sys
import tempfile
import threading
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dashboard.app import create_app,process_one
from test_dashboard import FakeEngine

with tempfile.TemporaryDirectory() as temp:
    app=create_app({'DB':str(Path(temp)/'leads.sqlite3'),'ENGINE':FakeEngine(),'TESTING':True})
    server=make_server('127.0.0.1',8091,app,threaded=True)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1440,'height':1000},device_scale_factor=1)
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto('http://localhost:8091');page.wait_for_function("() => document.querySelector('#health').textContent.includes('متصل')")
        assert page.locator('html').get_attribute('dir')=='rtl'
        page.locator('.nav[data-view=search]').click()
        for name,value in {'name':'پروژهٔ نمایشی · دادهٔ آزمایشی','country':'Iraq','category':'قطعات تراکتور','cities':'Baghdad','keywords':'قطع غيار جرارات\ntractor spare parts'}.items():
            page.locator(f'#search-form [name={name}]').fill(value)
        page.locator('[name=consent]').check();page.locator('#search-form [type=submit]').click()
        page.wait_for_function("() => document.querySelectorAll('#job-rows tr').length===2")
        process_one(app);process_one(app)
        page.locator('#refresh-history').click()
        page.wait_for_function("() => document.querySelector('#job-rows').textContent.includes('پایان‌یافته')")
        page.locator('.nav[data-view=leads]').click();page.wait_for_selector('#lead-rows button')
        assert page.locator('#lead-rows tr').count()==1
        page.locator('#lead-rows button').click();page.locator('#edit-form [name=notes]').fill('تماس در هفتهٔ آینده')
        page.locator('#edit-form [name=status]').select_option('followup');page.locator('#edit-form [type=submit]').click()
        page.wait_for_function("() => document.querySelector('#lead-rows').textContent.includes('تماس در هفتهٔ آینده')")
        for kind in ('csv','xlsx'):
            with page.expect_download() as d:page.locator('#export-'+kind).click()
            path=Path(temp)/('download.'+kind);d.value.save_as(path);assert path.stat().st_size>100
        page.locator('.nav[data-view=overview]').click();page.wait_for_function("() => document.querySelector('#stat-leads').textContent==='۱'")
        target=Path(os.environ.get('SCREENSHOT_PATH','/workspace/artifacts/azar-dashboard-preview.png'));target.parent.mkdir(parents=True,exist_ok=True);page.screenshot(path=str(target),full_page=True)
        page.set_viewport_size({'width':390,'height':844});page.screenshot(path=str(target.with_name('azar-dashboard-mobile.png')),full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        assert not errors,errors
        print('PASS: RTL, multilingual project, completion, deduplication, editing, downloads, desktop and mobile; no JS errors')
        browser.close()
    server.shutdown()
