"""Exercise the supplied Google shell and command dispatch with all traffic mocked."""
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SITE = 'https://usernameofficial.github.io/padlet/'
BOARD = 'https://padlet.com/example/board'


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1366, 'height': 900})
        page = context.new_page()
        calls, unexpected, errors = [], [], []
        state = dict(ready=False, running=False, opening=False, phase='waiting', board=None,
                     sent=0, errors=0, limit=0, logs=[], access=dict(known=True, remaining=10,
                     expires=None, can_run=True, post_limit=5000))

        def route(route):
            request = route.request
            url = urlsplit(request.url)
            if url.hostname == 'padlet-sender-panel.onrender.com':
                calls.append((url.path, request.post_data_json if request.post_data else None))
                if url.path == '/api/auth/login':
                    return route.fulfill(json={'token': 'fixture-session'})
                assert request.headers.get('authorization') == 'Bearer fixture-session'
                if url.path == '/api/run':
                    assert request.headers.get('x-request-id')
                    state.update(opening=True, phase='preparing')
                elif url.path == '/api/stop':
                    state.update(opening=False, phase='stopped')
                elif url.path != '/api/state':
                    raise AssertionError(url.path)
                return route.fulfill(json=state)
            if url.hostname != 'usernameofficial.github.io':
                unexpected.append(request.url)
                return route.abort()
            name = url.path.removeprefix('/padlet/') or 'index.html'
            assert '/' not in name
            content = ROOT / 'web' / name
            assert content.is_file(), name
            mime = {'.html': 'text/html', '.css': 'text/css', '.js': 'application/javascript'}[content.suffix]
            route.fulfill(content_type=mime, body=content.read_text(encoding='utf-8'))

        context.route('**/*', route)
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(SITE)
        page.locator('#accessKey').fill('A1B2C')
        page.locator('#keySubmit').click()
        page.wait_for_function("() => !document.getElementById('start').disabled")
        assert '비밀번호가 있는 게시판' in page.locator('.disguise-help').inner_text()
        # Nothing is configured in the main panel: search commands supply every field.
        assert page.locator('#subject').input_value() == ''
        assert page.locator('#body').input_value() == ''
        assert page.locator('#url').input_value() == ''
        page.locator('#disguise').click()
        page.wait_for_url('**/Google.html')
        field = page.locator('#commandInput')
        field.wait_for()
        assert page.title() == 'Google'
        assert not page.locator('iframe').count()
        assert not page.locator('script:not([src])').count()
        assert not page.locator('a[href]').count()
        assert '@gmail.com' not in page.content()
        before = len(calls)
        page.wait_for_timeout(250)
        assert len(calls) == before
        # Clicking every apparent button/link must neither navigate nor send a command.
        field.fill(f'{BOARD}, Search title, Search body, 10, 0.5')
        page.evaluate("document.querySelectorAll('a,button,input,[role=button]').forEach(el=>el.click())")
        assert page.url == SITE + 'Google.html' and len(calls) == before
        for invalid in ['hello', f'{BOARD}, 10, .5', f'{BOARD}, password, 10, .5',
                        f'{BOARD}, title, body, 1.5, .3', f'{BOARD}, title, body, -1, .3',
                        f'{BOARD}, title, body, 10, NaN', f'{BOARD}, title, body, 10, Infinity',
                        f'{BOARD}, title, body, 10, 86401', f'{BOARD}, , title, body, 10, .5',
                        f'{BOARD}, title, body, 1000001, .5', f'{BOARD}, , , 10, .5',
                        f'{BOARD}, "unclosed, body, 10, .5', f'{BOARD}, (unclosed, body, 10, .5',
                        f'{BOARD}, "title"junk, body, 10, .5',
                        'https://padlet.com.evil.example/a/b, title, body, 10, .5',
                        'https://evil.example/a/b, title, body, 10, .5',
                        'https://user:pass@padlet.com/a/b, title, body, 10, .5']:
            field.fill(invalid)
            field.press('Enter')
        assert len(calls) == before
        # IME confirmation cannot accidentally launch a job.
        field.fill(f'{BOARD}, Search title, Search body, 10, .5')
        field.dispatch_event('keydown', {'key': 'Enter', 'isComposing': True})
        assert len(calls) == before
        field.press('Enter')
        page.wait_for_function("() => document.getElementById('commandInput').value === ''")
        page.wait_for_timeout(200)
        runs = [data for path, data in calls if path == '/api/run']
        assert runs == [dict(url=BOARD, limit=10, delay=.5, subject='Search title', body='Search body')]
        field.fill(f'{BOARD}, Search title, Search body, 10, .5')
        field.press('Enter')
        page.wait_for_timeout(200)
        assert len([1 for path, _ in calls if path == '/api/run']) == 1
        page.go_back()
        page.locator('#stop').wait_for()
        page.wait_for_function("() => !document.getElementById('stop').disabled")
        assert page.locator('#subject').input_value() == ''
        page.locator('#stop').click()
        page.wait_for_function("() => document.getElementById('phase').textContent.includes('중지')")
        page.locator('#disguise').click()
        page.wait_for_url('**/Google.html')
        field = page.locator('#commandInput')
        field.fill(f'({BOARD}), (fixture-password), (New title), (New body), (3), (0.25)')
        field.press('Enter')
        page.wait_for_timeout(250)
        runs = [data for path, data in calls if path == '/api/run']
        assert len(runs) == 2
        assert runs[-1] == dict(url=BOARD, password='fixture-password', limit=3, delay=.25,
                               subject='New title', body='New body')
        assert field.input_value() == ''
        assert 'fixture-password' not in page.evaluate('JSON.stringify({...sessionStorage,...localStorage})')
        assert 'fixture-password' not in page.url
        # A later search can supply new content, commas and quotes without visiting the panel.
        state.update(opening=False, phase='finished')
        field.fill(f'{BOARD}, "pass,word", "제목, 하나", "본문에 ""따옴표""", 2, 1')
        field.press('Enter')
        page.wait_for_timeout(200)
        runs = [data for path, data in calls if path == '/api/run']
        assert len(runs) == 3
        assert runs[-1] == dict(url=BOARD, password='pass,word', subject='제목, 하나',
                               body='본문에 "따옴표"', limit=2, delay=1)
        assert page.evaluate('JSON.stringify({...sessionStorage,...localStorage})') == '{"padlet-session":"fixture-session"}'
        page.screenshot(path=str(ROOT / '.local/disguise-desktop.png'))
        page.set_viewport_size({'width': 390, 'height': 844})
        page.screenshot(path=str(ROOT / '.local/disguise-mobile.png'))
        assert page.evaluate('document.documentElement.scrollWidth') <= 390, page.evaluate("[...document.querySelectorAll('*')].filter(e=>e.getBoundingClientRect().right>390).map(e=>[e.tagName,e.className,e.getBoundingClientRect().width]).slice(0,15)")
        # Direct entry remains static, and commands cannot bypass key authentication.
        page.evaluate('sessionStorage.clear()')
        page.reload()
        before = len(calls)
        page.locator('#commandInput').fill(f'{BOARD}, title, body, 1, 1')
        page.locator('#commandInput').press('Enter')
        page.wait_for_timeout(150)
        assert len(calls) == before
        assert not unexpected, unexpected
        assert not errors, errors
        print(json.dumps({'disguise': True, 'static_google': True, 'both_command_formats': True,
                          'invalid_commands_inert': True, 'key_required': True, 'no_main_setup': True,
                          'password_not_stored': True, 'back_and_stop': True, 'real_posts': 0}))
        browser.close()


if __name__ == '__main__':
    main()
