"""Bounded CDP page capture in an owned Chrome profile; never uses a live browser."""
import os,shutil,subprocess
from pathlib import Path
import pytest

def capture(chrome,work,page,screenshot,report_selector,width,height):
    node=shutil.which('node')
    if not node:pytest.skip('Node.js is required for the optional Chrome CDP check')
    profile=Path(work)/'cdp-profile'
    process=subprocess.Popen([chrome,'--headless=new','--no-first-run','--disable-background-networking',
        '--disable-component-update','--disable-extensions','--disable-gpu','--hide-scrollbars',
        f'--window-size={width},{height}','--remote-debugging-address=127.0.0.1','--remote-debugging-port=0',
        '--user-data-dir='+str(profile),'about:blank'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
        **({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}))
    try:
        result=subprocess.run([node,str(Path(__file__).with_name('capture-browser.mjs')),str(profile),page.as_uri(),
            str(screenshot),report_selector],capture_output=True,text=True,encoding='utf-8',errors='strict',timeout=55,
            **({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}))
        assert result.returncode==0,'Isolated Chrome/CDP capture failed: '+result.stderr[-1200:]
        assert screenshot.is_file(),'Chrome did not capture the page'
        return result
    finally:
        try:process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
