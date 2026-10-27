import asyncio
import datetime as dt
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.core import UTC, iso
from app.one_check import operational_observation, run, save_cycle
from app.probe import classify_access

ROOT = Path(__file__).resolve().parent.parent
NOW = dt.datetime(2026, 10, 27, 19, tzinfo=UTC)


def good_flow():
    return [{'kind': kind, 'url': url, 'status': 'ok', 'at': iso(NOW)} for kind, url in [
        ('home', 'https://soluparts.cl/'), ('search', 'https://soluparts.cl/Product/Search.rails?w=FILTRO'),
        ('product', 'https://soluparts.cl/Product/Details.rails?id=1'),
        ('product', 'https://soluparts.cl/Product/Details.rails?id=2'),
        ('product', 'https://soluparts.cl/Product/Details.rails?id=3')]]


class FreeMonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.config = json.loads((ROOT / 'config.json').read_text())

    def tearDown(self):
        self.temp.cleanup()

    def test_requires_real_full_flow_not_just_a_200_home_page(self):
        self.assertFalse(operational_observation(good_flow()[:1]))
        self.assertFalse(operational_observation(good_flow()[:4]))
        self.assertTrue(operational_observation(good_flow()))

    def test_three_distinct_products_are_required(self):
        observations = good_flow()
        observations[-1]['url'] = observations[-2]['url']
        self.assertFalse(operational_observation(observations))

    def test_security_block_is_not_a_verified_monitor(self):
        observations = good_flow()
        observations[0]['status'] = 'blocked'
        self.assertFalse(operational_observation(observations))
        self.assertEqual(classify_access(403, 'Forbidden', 'Forbidden'), 'http_error')
        self.assertEqual(classify_access(403, 'Attention Required! | Cloudflare',
                                        'Sorry, you have been blocked'), 'blocked')

    def test_stops_without_requesting_the_site_after_deadline(self):
        with patch('app.one_check.probe', new_callable=AsyncMock) as test_probe:
            value = asyncio.run(run(self.path, self.config,
                    now=dt.datetime(2026, 11, 25, 2, tzinfo=UTC)))
            self.assertTrue(value['skipped'])
            test_probe.assert_not_called()

    def test_persists_real_observation_and_preserves_last_good_baseline(self):
        first = save_cycle(self.path, self.config, good_flow(), [], NOW, 5, '100-1', {})
        self.assertTrue(first['operational_observation'])
        state = json.loads((self.path / 'state.json').read_text())
        save_cycle(self.path, self.config, [{'kind':'home','url':'https://soluparts.cl/',
                   'status':'blocked','at':iso(NOW)}], [], NOW, 2, '101-1', state)
        state = json.loads((self.path / 'state.json').read_text())
        self.assertEqual(state['last_observation']['status'], 'blocked')
        self.assertEqual(state['last_operational_observation']['id'], '100-1')
        self.assertEqual(len((self.path / 'history.ndjson').read_text().splitlines()), 2)
        with self.assertRaises(ValueError):
            save_cycle(self.path, self.config, good_flow(), [], NOW, 5, '100-1', state)

    def test_keeps_failed_first_attempt_and_marks_limited_diagnostics(self):
        observations = good_flow()
        observations[0].update(status='transient', first_attempt={
            'kind':'home','url':'https://soluparts.cl/','status':'http_error','http_status':503},
            script_errors=['x' * 2000] * 20)
        result = save_cycle(self.path, self.config, observations, [], NOW, 5, '200-1', {})
        item = result['observations'][0]
        self.assertEqual(item['first_attempt']['http_status'], 503)
        self.assertEqual(item['script_errors_count'], 20)
        self.assertEqual(len(item['script_errors']), 5)
        self.assertFalse(item['html_retained'])

    def test_probe_failure_is_saved_as_monitor_error_and_does_not_verify_operation(self):
        with patch('app.one_check.probe', new_callable=AsyncMock, side_effect=RuntimeError('fixture error')):
            result = asyncio.run(run(self.path, self.config, now=NOW, run_id='300-1'))
        self.assertEqual(result['status'], 'monitor_error')
        self.assertFalse(result['operational_observation'])

    def test_data_branch_bootstrap_and_second_commit_preserve_history(self):
        origin = self.path / 'origin.git'
        checkout = self.path / 'source'
        runner = self.path / 'runner'
        runner.mkdir()
        env = os.environ | {'RUNNER_TEMP':str(runner), 'GITHUB_RUN_ID':'123'}
        def git(*args, cwd=None):
            return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True, text=True).stdout
        git('init', '--bare', str(origin))
        git('clone', str(origin), str(checkout))
        git('config','user.name','Local test',cwd=checkout)
        git('config','user.email','test@example.invalid',cwd=checkout)
        (checkout / 'README.md').write_text('Local fixture')
        git('add','README.md',cwd=checkout)
        git('commit','-m','Fixture',cwd=checkout)
        git('push','origin','HEAD:refs/heads/main',cwd=checkout)
        for number in (1,2):
            subprocess.run(['bash',str(ROOT/'scripts/prepare_store.sh')],cwd=checkout,env=env,
                           check=True,capture_output=True,text=True)
            target = runner / 'soluparts-data'
            (target / 'records').mkdir(exist_ok=True)
            (target / 'records' / f'{number}.json').write_text(json.dumps({'test':number}))
            subprocess.run(['bash',str(ROOT/'scripts/publish_store.sh')],cwd=checkout,env=env,
                           check=True,capture_output=True,text=True)
            git('worktree','remove',str(target),cwd=checkout)
        names = git('ls-tree','-r','--name-only','refs/remotes/origin/monitor-data',cwd=checkout)
        self.assertIn('records/1.json', names)
        self.assertIn('records/2.json', names)
        self.assertNotIn('README.md', names)


if __name__ == '__main__':
    unittest.main()
