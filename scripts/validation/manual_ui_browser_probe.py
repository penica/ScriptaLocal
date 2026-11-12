"""Real browser submission smoke test. Tokens/readings never go to stdout."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

with sync_playwright() as playwright:
    browser=playwright.chromium.launch(args=['--no-sandbox'])
    context=browser.new_context(viewport={'width':1280,'height':900},accept_downloads=True)
    page=context.new_page();errors=[];uploads=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.on('request',lambda request:uploads.append(request.url) if request.method=='POST' and request.url.endswith('/v1/jobs') else None)
    page.goto('https://scripta.penic.net:8093/ui')
    page.locator('#login').wait_for(state='visible')
    page.screenshot(path='/output/sign-in.png')
    page.locator('#key').fill(Path('/run/secrets/ui_key').read_text().strip())
    page.get_by_role('button',name='Open workspace').click()
    page.locator('#workspace').wait_for(state='visible')
    page.locator('#health').filter(has_text='Ready to transcribe').wait_for()
    page.locator('#settings').click()
    page.get_by_role('button',name='Save destination').click()
    page.get_by_role('status').filter(has_text='Backup destination saved').wait_for()
    page.locator('#image').set_input_files('/input/reference-crop.jpg')
    page.locator('#preview').wait_for(state='visible')
    page.get_by_role('button',name='Transcribe image').click()
    page.locator('#results').wait_for(state='visible',timeout=180000)
    assert page.locator('.cell').count()==18
    page.screenshot(path='/output/results-desktop.png',full_page=True)
    with page.expect_download() as download:
        page.get_by_role('button',name='Download JSON').click()
    download.value.save_as('/output/export.json')
    result=json.loads(Path('/output/export.json').read_text());assert len(result['cells'])==18
    page.reload();page.locator('#results').wait_for(state='visible',timeout=15000)
    assert len(uploads)==1
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.screenshot(path='/output/results-mobile.png',full_page=True)
    page.get_by_role('button',name='Sign out').click()
    page.locator('#login').wait_for(state='visible')
    assert not errors,errors
    report={'real_browser':'Chromium','upload_requests':len(uploads),'cells':len(result['cells']),
            'lines':sum(len(cell['lines']) for cell in result['cells']),'job_id':result['job_id'],
            'json_download_passed':True,'reload_restores_job':True,'settings_saved':True,'mobile_390px_no_horizontal_overflow':True,
            'sign_out_passed':True,'page_errors':errors}
    Path('/output/browser-proof.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report));browser.close()
