"""Boundary and regression tests for the public-only profile renderer."""
import base64
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('sync_profile', ROOT/'scripts/sync_profile.py')
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)

def repository(name='public-game', repo_id=666255199, **overrides):
    value = {'id': repo_id, 'name': name, 'full_name': 'hamletroyophelia/'+name,
             'owner': {'id': 119651951, 'login': 'hamletroyophelia'},
             'private': False, 'visibility': 'public', 'fork': False, 'archived': False,
             'description': 'A public project', 'language': 'Python',
             'stargazers_count': 1, 'forks_count': 0,
             'pushed_at': '2026-10-06T01:32:46Z', 'created_at': '2023-07-15T00:00:00Z'}
    value.update(overrides)
    return value

class Response:
    status = 200
    def __init__(self, url, value): self.url, self.data = url, json.dumps(value).encode()
    def geturl(self): return self.url
    def read(self, size): return self.data[:size]
    def __enter__(self): return self
    def __exit__(self, *args): pass

class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)/'profile'
        shutil.copytree(ROOT, self.root, ignore=shutil.ignore_patterns('__pycache__', '.git'))
        self.config = sync.load_config(self.root/'config/profile.json')
    def tearDown(self): self.tmp.cleanup()
    def run_sync(self, raw=None, manifests=None, **kwargs):
        return sync.run(self.root, raw=[repository()] if raw is None else raw,
                        manifest_evidence=[] if manifests is None else manifests, **kwargs)
    def files(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
    def test_excludes_private_fork_archived_foreign_and_profile(self):
        rows = [repository(), repository('secret', 2, private=True, visibility='private', description='PRIVATE_TOKEN'),
                repository('fork', 3, fork=True), repository('archive', 4, archived=True),
                repository('foreign', 5, owner={'id': 1, 'login': 'other'}),
                repository('hamletroyophelia', 6)]
        self.assertEqual([r['name'] for r in sync.normalize_public(rows, self.config)], ['public-game'])
        self.run_sync(rows)
        generated = json.loads((self.root/'data/generated-files.json').read_text())['files']
        self.assertFalse(any('PRIVATE_TOKEN' in (self.root/p).read_text() for p in generated))
    def test_visibility_and_owner_id_fail_closed(self):
        self.assertEqual(sync.normalize_public([repository(visibility=None), repository(owner={'id': 1, 'login': 'hamletroyophelia'})], self.config), [])
    def test_canonical_repository_identity_required(self):
        with self.assertRaises(ValueError): self.run_sync([repository(full_name='other/public-game')])
    def test_repository_path_traversal_rejected(self):
        with self.assertRaises(ValueError): self.run_sync([repository(name='../private')])
    def test_duplicate_repository_ids_rejected(self):
        with self.assertRaises(ValueError): self.run_sync([repository(), repository('duplicate')])
    def test_non_integer_star_counts_rejected(self):
        for value in (-1, True, '100'):
            with self.subTest(value=value), self.assertRaises(ValueError): self.run_sync([repository(stargazers_count=value)])
    def test_noop_preserves_observation_time_and_files(self):
        self.run_sync(now='2026-10-06T00:00:00Z')
        before = self.files()
        result = self.run_sync(now='2026-10-06T23:59:59Z')
        self.assertEqual(result['changed_files'], [])
        self.assertEqual(before, self.files())
    def test_offline_window_advances_and_counts_stay_unknown(self):
        result = sync.offline_activity('2026-10-07T01:17:00Z')
        self.assertEqual(result['window_start'], '2026-09-08')
        self.assertEqual(result['window_end'], '2026-10-07')
        self.assertIsNone(result['created_prs'])
    def extended_opener(self, count=0, incomplete=False, fail_profile=False):
        def opener(request, timeout):
            self.assertNotIn('Authorization', request.headers)
            self.assertIn('https://api.github.com/', request.full_url)
            if '/users/' in request.full_url:
                if fail_profile: raise OSError('network unavailable')
                data = {'id':119651951,'login':'hamletroyophelia','followers':0,'following':1}
            else:
                self.assertIn('is%3Apublic', request.full_url)
                self.assertIn('-repo%3Ahamletroyophelia%2Fhamletroyophelia',request.full_url)
                data = {'total_count':count,'incomplete_results':incomplete,'items':[{'private_data':'NEVER_PERSIST'}]}
            return Response(request.full_url,data)
        return opener
    def test_public_search_zero_is_verified_zero(self):
        result=sync.fetch_extended(self.config,'2026-10-06T01:17:00Z',self.extended_opener())
        self.assertEqual(result['authored_commits'],0)
        self.assertEqual(result['created_prs'],0)
        self.assertEqual(result['following'],1)
        self.assertEqual(result['unavailable'],{})
        self.assertNotIn('NEVER_PERSIST',json.dumps(result))
    def test_incomplete_search_is_unknown_not_zero(self):
        result=sync.fetch_extended(self.config,'2026-10-06T01:17:00Z',self.extended_opener(count=8,incomplete=True))
        self.assertIsNone(result['authored_commits'])
        self.assertIsNone(result['created_issues'])
        self.assertIn('created_prs',result['unavailable'])
    def test_unavailable_profile_is_unknown_not_zero(self):
        result=sync.fetch_extended(self.config,'2026-10-06T01:17:00Z',self.extended_opener(fail_profile=True))
        self.assertIsNone(result['followers'])
        self.assertEqual(result['created_prs'],0)
    def test_extended_identity_mismatch_aborts(self):
        def opener(request, timeout):
            return Response(request.full_url,{'id':1,'login':'hamletroyophelia','followers':0,'following':0})
        with self.assertRaisesRegex(ValueError,'identity mismatch'):
            sync.fetch_extended(self.config,'2026-10-06T01:17:00Z',opener)
    def test_fork_totals_are_scoped_to_original_projects(self):
        self.run_sync([repository(forks_count=2),repository('fork-copy',2,fork=True,forks_count=100)])
        stats=json.loads((self.root/'data/public-snapshot.json').read_text())['metrics']
        self.assertEqual(stats['total_forks'],2)
        self.assertEqual(stats['public_owned_count'],2)
    def test_star_change_updates_stats_and_achievement(self):
        self.run_sync([repository(stargazers_count=0)])
        self.assertFalse((self.root/'assets/generated/achievement-first-echo.svg').exists())
        self.run_sync([repository(stargazers_count=10)])
        self.assertTrue((self.root/'assets/generated/achievement-ten-echoes.svg').exists())
        self.assertIn('累计星标 10', (self.root/'README.md').read_text())
    def test_private_transition_removes_previous_public_references(self):
        self.run_sync()
        result = self.run_sync([repository(private=True, visibility='private')])
        self.assertEqual(result['project_count'], 0)
        self.assertFalse((self.root/'assets/generated/achievement-first-flight.svg').exists())
        self.assertNotIn('/public-game', (self.root/'README.md').read_text())
    def test_repository_rename_keeps_stable_id_achievement(self):
        self.run_sync([repository('renamed-game')])
        readme = (self.root/'README.md').read_text()
        self.assertIn('/renamed-game', readme)
        self.assertTrue((self.root/'assets/generated/achievement-first-flight.svg').exists())
    def test_untrusted_description_is_rendered_as_text(self):
        self.run_sync([repository(description='<script>alert(1)</script> [click](https://bad.example) **bold**')])
        readme = (self.root/'README.md').read_text()
        self.assertNotIn('<script>', readme)
        self.assertIn('&lt;script&gt;', readme)
        self.assertIn('\\[click\\]', readme)
    def test_language_removed_deletes_stale_badge(self):
        self.run_sync([repository(language='Rust')])
        old = self.root/('assets/generated/tech-'+sync.technology_slug('Rust')+'.svg')
        self.assertTrue(old.exists())
        self.run_sync([repository(language='Python')])
        self.assertFalse(old.exists())
    def test_manifest_dependencies_update_and_remove_tags(self):
        repo = repository()
        evidence = [{'repository_id': repo['id'], 'path': 'package.json', 'technologies': ['React', 'Electron'], 'source_sha': 'a'*40}]
        self.run_sync([repo], evidence)
        self.assertIn('**公开依赖** — Electron · React', (self.root/'README.md').read_text())
        self.run_sync([repo], [])
        self.assertFalse((self.root/('assets/generated/tech-'+sync.technology_slug('React')+'.svg')).exists())
    def test_out_of_scope_manifest_evidence_rejected(self):
        with self.assertRaises(ValueError): self.run_sync(manifests=[{'repository_id': 1, 'path': 'package.json', 'technologies': ['React']}])
    def test_unknown_dependency_evidence_rejected(self):
        with self.assertRaises(ValueError): self.run_sync(manifests=[{'repository_id': 666255199, 'path': 'package.json', 'technologies': ['fake-skill']}])
    def test_network_failure_preserves_last_good_files(self):
        self.run_sync()
        before = self.files()
        def fail(*args, **kwargs): raise TimeoutError('network unavailable')
        with self.assertRaises(TimeoutError): sync.run(self.root, opener=fail)
        self.assertEqual(before, self.files())
    def test_manifest_failure_preserves_last_good_files(self):
        self.run_sync()
        before = self.files()
        with patch.object(sync, 'fetch_manifests', side_effect=ValueError('invalid manifest')):
            with self.assertRaises(ValueError): sync.run(self.root, raw=[repository(stargazers_count=8)])
        self.assertEqual(before, self.files())
    def test_invalid_markers_fail_before_fetch_or_write(self):
        readme = self.root/'README.md'
        readme.write_text(readme.read_text().replace('PROFILE-SYNC:projects:END', 'BROKEN'))
        before = self.files()
        with patch.object(sync, 'fetch_public') as fetch:
            with self.assertRaises(ValueError): sync.run(self.root)
            fetch.assert_not_called()
        self.assertEqual(before, self.files())
    def test_unsafe_generated_manifest_does_not_delete(self):
        (self.root/'data/generated-files.json').write_text(json.dumps({'files': ['../private-file']}))
        before = self.files()
        with self.assertRaises(ValueError): self.run_sync()
        self.assertEqual(before, self.files())
    def test_pagination_and_never_sends_credentials(self):
        requests = []
        def open_page(request, **kwargs):
            requests.append(request)
            return Response(request.full_url, [repository()]*100 if len(requests)==1 else [])
        with patch.dict(os.environ, {'GITHUB_TOKEN': 'DO_NOT_SEND'}):
            self.assertEqual(len(sync.fetch_public('hamletroyophelia', opener=open_page)), 100)
        self.assertEqual(len(requests), 2)
        self.assertFalse(any('authorization' in {k.lower() for k in r.headers} for r in requests))
    def test_incomplete_pagination_is_rejected(self):
        count = 0
        def open_page(request, **kwargs):
            nonlocal count
            count += 1
            if count == 2: raise TimeoutError()
            return Response(request.full_url, [repository()]*100)
        with self.assertRaises(TimeoutError): sync.fetch_public('hamletroyophelia', opener=open_page)
    def test_empty_public_snapshot_valid(self):
        result = self.run_sync([])
        self.assertEqual((result['project_count'], result['total_stars'], result['unlocked_achievements']), (0, 0, 0))
    def test_language_counts_are_repository_counts(self):
        rows = [repository('a', 1), repository('b', 2), repository('c', 3, language='Swift')]
        result = sync.metrics(sync.normalize_public(rows, self.config))
        self.assertEqual(result['primary_languages'], {'Python': 2, 'Swift': 1})
    def test_supported_manifest_parsers(self):
        samples = [('package.json', '{"private":true,"dependencies":{"react":"*"},"devDependencies":{"electron":"*","vite":"*"}}', ['Electron','React','Vite']),
                   ('pyproject.toml', '[project]\ndependencies=["numpy>=2","pygame"]', ['NumPy','Pygame']),
                   ('requirements.txt', 'matplotlib>=3\n-e git+https://example.invalid\n# comment', ['Matplotlib']),
                   ('Cargo.toml', '[dependencies]\nserde="1"\ntokio="1"', ['Serde','Tokio'])]
        for name, text, expected in samples:
            with self.subTest(path=name): self.assertEqual(sync.dependency_names(name, text), expected)
    def test_manifest_fetch_is_anonymous_and_root_only(self):
        requests = []
        def opener(request, **kwargs):
            requests.append(request)
            if request.full_url.endswith('/contents'):
                return Response(request.full_url, [{'type':'file','path':'package.json','name':'package.json'},
                                                   {'type':'dir','path':'private-looking-directory','name':'private-looking-directory'}])
            return Response(request.full_url, {'type':'file','path':'package.json','encoding':'base64',
                                              'content':base64.b64encode(b'{"dependencies":{"react":"*"}}').decode(), 'sha':'a'*40})
        repos = sync.normalize_public([repository()], self.config)
        with patch.dict(os.environ, {'GITHUB_TOKEN':'DO_NOT_SEND'}):
            result = sync.fetch_manifests(self.config, repos, opener)
        self.assertEqual(result[0]['technologies'], ['React'])
        self.assertEqual(len(requests), 2)
        self.assertFalse(any('authorization' in {k.lower() for k in r.headers} for r in requests))
    def test_workflow_is_repository_scoped_and_minimal(self):
        path = self.root/'automation/profile-sync.yml.disabled'
        if not path.exists(): path = self.root/'.github/workflows/profile-sync.yml'
        text = path.read_text()
        self.assertIn('permissions: {}', text)
        self.assertEqual(text.count('contents: write'), 1)
        self.assertIn("github.repository == 'hamletroyophelia/hamletroyophelia'", text)
        self.assertIn('github.event.repository.default_branch', text)
        self.assertIn('actions/checkout@11d5960a326750d5838078e36cf38b85af677262', text)
        self.assertNotIn('secrets.', text)
        self.assertNotIn('--force', text)
    def test_manual_biography_and_music_equipment_preserved(self):
        self.run_sync()
        text = (self.root/'README.md').read_text()
        self.assertIn('DDLC，尤其是 Monika', text)
        self.assertIn('Synthesizer V · REAPER · MIDI', text)

if __name__ == '__main__': unittest.main()
