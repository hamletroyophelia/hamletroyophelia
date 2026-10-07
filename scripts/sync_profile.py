#!/usr/bin/env python3
"""Render a public-only profile snapshot. No credentials, third-party services or dependencies."""
from __future__ import annotations

import argparse
import base64
from collections import Counter
from datetime import datetime, timezone, timedelta
import hashlib
import html
import json
from pathlib import Path
import re
import sys
import tomllib
from urllib.request import Request, urlopen
from urllib.parse import quote

COLORS = {'mint': '#a3ebcb', 'rose': '#edb6ce', 'gold': '#f4d58d', 'purple': '#c6b7ed'}
METRICS = {'project_count', 'total_stars', 'language_count'}
MARKERS = ('projects', 'techstack', 'achievements')
GENERATED_FILES = ('README.md', 'data/public-snapshot.json', 'data/achievement-evidence.json', 'data/generated-files.json')

def plain(value, limit=300):
    """Strip controls; descriptions become escaped text, never markup or executable code."""
    if not isinstance(value, str): return ''
    return re.sub(r'\s+', ' ', ''.join(c for c in value if ord(c) >= 32)).strip()[:limit]

def md(value):
    s = html.escape(plain(value), quote=True)
    return re.sub(r'([\\`*_\[\]{}|])', r'\\\1', s)

def load_config(path):
    config = json.loads(path.read_text())
    if config.get('schema_version') != 1: raise ValueError('Unsupported config schema')
    if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})', config.get('owner', '')): raise ValueError('Invalid owner')
    if not isinstance(config.get('owner_id'), int): raise ValueError('Missing stable owner ID')
    if not 1 <= config.get('recent_limit', 5) <= 10: raise ValueError('recent_limit must be 1..10')
    if not 1 <= config.get('manifest_repository_limit', 8) <= 8: raise ValueError('Manifest scope must be 1..8 repositories')
    seen = set()
    for a in config.get('achievements', []):
        if not re.fullmatch(r'[a-z0-9-]+', a['id']) or a['id'] in seen: raise ValueError('Invalid/duplicate achievement ID')
        seen.add(a['id'])
        if a['color'] not in COLORS: raise ValueError('Invalid achievement color')
        rule = a['rule']
        if rule['kind'] == 'repository_public':
            if not isinstance(rule.get('repository_id'), int): raise ValueError('Missing repository ID')
        elif rule['kind'] == 'metric_at_least':
            if rule.get('metric') not in METRICS or type(rule.get('threshold')) is not int or rule['threshold'] < 1: raise ValueError('Invalid metric rule')
        else: raise ValueError('Unsupported achievement rule')
    return config

def fetch_public(owner, opener=urlopen):
    """Only the anonymous public-user endpoint; intentionally ignore GITHUB_TOKEN."""
    result = []
    for page in range(1, 11):
        url = f'https://api.github.com/users/{owner}/repos?type=owner&sort=updated&direction=desc&per_page=100&page={page}'
        request = Request(url, headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'Roy-Public-Profile-Sync', 'X-GitHub-Api-Version': '2026-03-10'})
        with opener(request, timeout=20) as response:
            if response.status != 200: raise ValueError('Public API did not return HTTP 200')
            final = response.geturl()
            if not final.startswith('https://api.github.com/users/' + owner + '/repos?'): raise ValueError('Unexpected API redirect')
            raw = response.read(8_000_001)
            if len(raw) > 8_000_000: raise ValueError('Public API response too large')
            batch = json.loads(raw)
        if not isinstance(batch, list): raise ValueError('Public API returned an invalid list')
        result.extend(batch)
        if len(batch) < 100: return result
    raise ValueError('Repository pagination limit reached; refusing a partial snapshot')

def normalize_public(raw, config):
    """Allowlist public-owned, non-fork, active metadata before storing anything."""
    result = []
    seen = set()
    for repo in raw:
        if not isinstance(repo, dict): raise ValueError('Repository entry must be an object')
        owner = repo.get('owner') or {}
        if repo.get('private') is not False or repo.get('visibility') != 'public': continue
        if owner.get('id') != config['owner_id'] or str(owner.get('login', '')).lower() != config['owner'].lower(): continue
        if config.get('exclude_forks', True) and repo.get('fork') is not False: continue
        if config.get('exclude_archived', True) and repo.get('archived') is not False: continue
        name = repo.get('name')
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', name): raise ValueError('Invalid public repository name')
        if name.lower() == config['profile_repository'].lower(): continue
        repo_id = repo.get('id')
        if type(repo_id) is not int or repo_id in seen: raise ValueError('Missing or duplicate public repository ID')
        seen.add(repo_id)
        full_name = f"{config['owner']}/{name}"
        if repo.get('full_name', '').lower() != full_name.lower(): raise ValueError('Repository identity mismatch')
        def integer(key):
            value = repo.get(key, 0)
            if type(value) is not int or value < 0: raise ValueError('Invalid public metric')
            return value
        def date(key):
            value = repo.get(key)
            if value is None: return None
            if not isinstance(value, str): raise ValueError('Invalid repository date')
            datetime.fromisoformat(value.replace('Z', '+00:00'))
            return value
        result.append({'id': repo_id, 'name': name, 'full_name': full_name, 'url': f'https://github.com/{full_name}', 'description': plain(repo.get('description')), 'language': plain(repo.get('language'), 60) or None, 'stars': integer('stargazers_count'), 'forks': integer('forks_count'), 'pushed_at': date('pushed_at'), 'created_at': date('created_at')})
    return sorted(result, key=lambda x: x['id'])

def metrics(repos):
    languages = Counter(r['language'] for r in repos if r['language'])
    return {'project_count': len(repos), 'total_stars': sum(r['stars'] for r in repos), 'total_forks': sum(r['forks'] for r in repos), 'language_count': len(languages), 'primary_languages': dict(sorted(languages.items(), key=lambda x: (-x[1], x[0])))}

def fetch_extended(config, observed, opener=urlopen):
    owner = config['owner']
    end = datetime.fromisoformat(observed.replace('Z', '+00:00')).astimezone(timezone.utc).date()
    start = end-timedelta(days=29)
    result = {'window_start': start.isoformat(), 'window_end': end.isoformat(), 'window_days': 30, 'followers': None, 'following': None, 'authored_commits': None, 'created_prs': None, 'created_issues': None, 'sources': {}, 'unavailable': {}}
    profile_url = 'https://api.github.com/users/'+owner
    try:
        profile = public_json(profile_url, opener)
        if profile.get('id') != config['owner_id'] or profile.get('login', '').lower() != owner.lower(): raise ValueError('Public profile identity mismatch')
        for key in ('followers', 'following'):
            if type(profile.get(key)) is not int or profile[key] < 0: raise ValueError('Invalid public profile metric')
            result[key] = profile[key]
        result['sources']['followers'] = profile_url
    except ValueError: raise
    except Exception as error:
        result['unavailable']['profile'] = type(error).__name__
    period = start.isoformat()+'..'+end.isoformat()
    exclusion = ' -repo:'+owner+'/'+config['profile_repository']
    queries = {
        'authored_commits': ('commits', 'author:'+owner+' is:public author-date:'+period+exclusion),
        'created_prs': ('issues', 'author:'+owner+' is:pr is:public created:'+period+exclusion),
        'created_issues': ('issues', 'author:'+owner+' is:issue is:public created:'+period+exclusion),
    }
    for metric, (endpoint, query) in queries.items():
        url = 'https://api.github.com/search/'+endpoint+'?q='+quote(query, safe='')+'&per_page=1'
        result['sources'][metric] = url
        try:
            data = public_json(url, opener)
            if data.get('incomplete_results') is not False or type(data.get('total_count')) is not int or data['total_count'] < 0:
                raise ValueError('Incomplete or invalid public search count')
            result[metric] = data['total_count']
        except Exception as error:
            result['unavailable'][metric] = type(error).__name__
    return result

def offline_activity(observed):
    end = datetime.fromisoformat(observed.replace('Z', '+00:00')).astimezone(timezone.utc).date()
    return {'window_start': (end-timedelta(days=29)).isoformat(), 'window_end': end.isoformat(), 'window_days': 30, 'followers': None, 'following': None, 'authored_commits': None, 'created_prs': None, 'created_issues': None, 'sources': {}, 'unavailable': {'source': 'offline'}}

def art_image(name, x, y, width, height):
    # Original generated pixels are embedded; SVG data labels stay deterministic.
    path = Path(__file__).resolve().parents[1]/'assets/art'/f'{name}.png'
    encoded = base64.b64encode(path.read_bytes()).decode('ascii')
    return f'<image x="{x}" y="{y}" width="{width}" height="{height}" preserveAspectRatio="none" href="data:image/png;base64,{encoded}"/>'

def resolve_achievements(config, repos, stats):
    by_id = {r['id']: r for r in repos}
    evidence = []
    for achievement in config['achievements']:
        rule = achievement['rule']
        if rule['kind'] == 'repository_public':
            repo = by_id.get(rule['repository_id'])
            passed = repo is not None
            proof = {'repository_id': rule['repository_id'], 'public_repository': repo['url']} if repo else {'public_repository_present': False}
        else:
            observed = stats[rule['metric']]
            passed = observed >= rule['threshold']
            proof = {'metric': rule['metric'], 'observed': observed, 'threshold': rule['threshold']}
        evidence.append({'id': achievement['id'], 'title': achievement['title'], 'unlocked': passed, 'rule': rule, 'evidence': proof})
    return evidence

def achievement_svg(achievement):
    col = COLORS[achievement['color']]
    icon = {'map-maker':'map', 'first-echo':'star', 'language-runes':'runes', 'world-expansion':'portal', 'ten-echoes':'crown'}.get(achievement['id'], achievement['icon'])
    esc = lambda value: html.escape(plain(value), quote=True)
    body = art_image('achievement-frame', 0, 0, 480, 160)+art_image(icon, 32, 56, 72, 72)
    body += f'<g font-family="ui-monospace,SFMono-Regular,PingFang SC,monospace"><text x="120" y="81" font-size="26" font-weight="700" fill="#eff6ef">{esc(achievement['title'])}</text><text x="120" y="101" font-size="10" fill="{col}">{esc(achievement['english'])}</text><text x="120" y="123" font-size="14" fill="#c6cddd">{esc(achievement['description'])}</text></g>'
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="480" height="160" viewBox="0 0 480 160" role="img" aria-label="{esc(achievement["title"])}">{body}</svg>\n'

def card(body, label, height=500):
    # Nine-slice style: keep the pixel ornaments proportional, stretch only edges.
    encoded = base64.b64encode((Path(__file__).resolve().parents[1]/'assets/art/stats-frame.png').read_bytes()).decode('ascii')
    frame = ''
    for y, h, source_y, source_h in [(0,176,0,116),(176,height-330,116,92),(height-154,154,208,102)]:
        frame += f'<svg x="0" y="{y}" width="480" height="{h}" viewBox="0 {source_y} 317 {source_h}" preserveAspectRatio="none"><image width="317" height="310" href="data:image/png;base64,{encoded}"/></svg>'
    return '<svg xmlns="http://www.w3.org/2000/svg" width="480" height="'+str(height)+'" viewBox="0 0 480 '+str(height)+'" role="img" aria-label="'+html.escape(label, quote=True)+'">'+frame+'<g font-family="ui-monospace,SFMono-Regular,Consolas,PingFang SC,monospace">'+body+'</g></svg>\n'

def status_svg(stats, unlocked):
    body = '<text x="82" y="210" font-size="22" font-weight="700" fill="#edb6ce">Roy\'s Adventure Stats</text>'
    for i, (label, count) in enumerate([('公开原创项目', stats['project_count']), ('项目累计星标', stats['total_stars']), ('项目累计 Fork', stats['total_forks']), ('关注者 Followers', stats.get('followers')), ('已解锁成就', unlocked)]):
        y = 247 + i*23
        body += f'<text x="82" y="{y}" font-size="18" fill="#a7b8c8">{label}</text><text x="394" y="{y}" text-anchor="end" font-size="24" font-weight="700" fill="#a3ebcb">{count if count is not None else "—"}</text>'
    return card(body, '公开项目、星标与自定义成就统计')

def languages_svg(stats):
    entries = list(stats['primary_languages'].items())
    if len(entries) > 6: entries = entries[:5]+[('Other', sum(n for _, n in entries[5:]))]
    palette = ['#a3ebcb', '#edb6ce', '#f4d58d', '#c6b7ed', '#9cc9e9', '#d1d9e0']
    body = '<text x="82" y="210" font-size="22" font-weight="700" fill="#edb6ce">Language Inventory</text><text x="82" y="240" font-size="14" fill="#a7b8c8">仓库主要语言 · 按项目个数</text>'
    total = sum(n for _, n in entries)
    x = 82
    for i, (_, count) in enumerate(entries):
        width = 316*count/max(total, 1)
        body += f'<rect x="{x:.2f}" y="262" width="{width:.2f}" height="14" fill="{palette[i]}"/>'
        x += width
    for i, (name, count) in enumerate(entries):
        x, y = 82+(i%2)*166, 298+(i//2)*26
        label = html.escape(name[:18])
        body += f'<rect x="{x}" y="{y-12}" width="11" height="11" fill="{palette[i]}"/><text x="{x+19}" y="{y}" font-size="15" fill="#eff6ef">{label} · {count}</text>'
    if not entries: body += '<text x="82" y="212" font-size="19" fill="#a7b8c8">暂无公开语言数据</text>'
    return card(body, '公开仓库主要语言分布，按项目个数')

def activity_svg(stats, activity):
    body = '<text x="82" y="210" font-size="22" font-weight="700" fill="#edb6ce">30-Day Quest Activity</text>'
    body += f'<text x="82" y="240" font-size="13" fill="#a7b8c8">{activity["window_start"]} — {activity["window_end"]} UTC</text>'
    for i, (label, value) in enumerate([('公开检索提交', activity['authored_commits']), ('新建 PR', activity['created_prs']), ('新建 Issue', activity['created_issues']), ('更新原创项目', stats['updated_projects_30d'])]):
        y=274+i*23
        body += f'<text x="82" y="{y}" font-size="18" fill="#a7b8c8">{label}</text><text x="394" y="{y}" text-anchor="end" font-size="24" fill="#f4d58d">{value if value is not None else "—"}</text>'
    return card(body, '近30天公开提交、PR、Issue与项目更新活动')

def projects_section(config, repos, stats, activity):
    out = ['<p>', f'  <img src="assets/generated/status.svg" alt="公开原创项目 {stats["project_count"]} 个，累计星标 {stats["total_stars"]} 个" width="400">', '  <img src="assets/generated/languages.svg" alt="仓库主要语言分布，按项目个数" width="400">', '</p>', '', f'**公开原创项目 {stats["project_count"]} 个** · **累计星标 {stats["total_stars"]}** · **仓库主要语言 {stats["language_count"]} 种**', '', '### Recent quests · 最近更新', '']
    out[6:6] = ['', f'公开仓库共 **{stats["public_owned_count"]}** 个（含 fork、归档，排除主页）；本范围项目累计 Fork **{stats["total_forks"]}**。关注者 **{stats["followers"] if stats["followers"] is not None else "未知"}**，关注中 **{stats["following"] if stats["following"] is not None else "未知"}**。', '', '<p><img src="assets/generated/activity.svg" alt="近30天公开活动；未知值以破折号表示" width="400"></p>', '', f'统计窗口：**{activity["window_start"]} 至 {activity["window_end"]}（30 个 UTC 日期）**。提交数取本人署名、公开仓库默认分支的 GitHub 搜索索引；PR/Issue 是本人在公开仓库新建的数量。排除主页仓库，含其他公开仓库中的贡献，不含私有活动或未索引提交。', '', '<details>', '<summary><b>Stats journal · 覆盖范围与来源</b></summary>', '', f'- 更新的原创项目：{stats["updated_projects_30d"]}；依据纳入范围项目的最近 push 时间。']
    at = out.index('### Recent quests · 最近更新')
    for key, label in [('authored_commits','公开署名提交'),('created_prs','新建 PR'),('created_issues','新建 Issue')]:
        value = activity[key]
        url = activity['sources'].get(key)
        line = f'- {label}：'+(str(value) if value is not None else '未知（本轮 API 无完整结果）')
        if url: line += f'；[公开查询]({url})。'
        out.insert(at, line);at+=1
    out[at:at] = ['', '搜索索引可能滞后；不将其视为完整的 GitHub 贡献图统计。未知不等于 0。', '', '</details>', '']
    recent = sorted(repos, key=lambda r: (max(r['pushed_at'] or '', r['created_at'] or ''), r['name']), reverse=True)[:config['recent_limit']]
    for repo in recent:
        when = max(repo['pushed_at'] or '', repo['created_at'] or '')[:10] or '暂无日期'
        desc = md(repo['description']) or '公开项目；介绍待补充。'
        out.extend([f'**[{md(repo["name"])}]({repo["url"]})** · {md(repo["language"] or "未标注语言")} · ★ {repo["stars"]} · {when}', f'{desc}', ''])
    if not repos: out.extend(['目前没有符合公开范围的项目。', ''])
    by_id = {r['id']: r for r in repos}
    featured = [f for f in config['featured'] if f['repository_id'] in by_id]
    if featured: out.extend(['<details>', '<summary><b>Main quest stories · 作品介绍</b></summary>', ''])
    for item in featured:
        repo = by_id[item['repository_id']]
        out.extend([f'### {md(item["title"])}', '', f'**[{md(item["label"])}]({repo["url"]})** — {md(item["summary"])}', '', ' · '.join('`'+plain(t).replace('`', '')+'`' for t in item['tags']), ''])
    if featured: out.extend(['</details>', ''])
    return '\n'.join(out).rstrip()

def achievements_section(config, evidence, stats):
    out = ['由本次公开项目快照和明确规则计算；点击作品成就进入仓库。', '', '<p>']
    for a, proof in zip(config['achievements'], evidence):
        if not proof['unlocked']: continue
        src = f'assets/generated/achievement-{a["id"]}.svg'
        img = f'<img src="{src}" alt="{html.escape(a["title"]+"："+a["description"], quote=True)}" width="400">'
        url = proof['evidence'].get('public_repository')
        out.append('  '+(f'<a href="{url}">{img}</a>' if url else img))
    out.extend(['</p>', '', '<details>', '<summary><b>Achievement journal · 规则与解锁记录</b></summary>', ''])
    for a, proof in zip(config['achievements'], evidence):
        if proof['rule']['kind'] == 'repository_public':
            url = proof['evidence'].get('public_repository')
            detail = f'本次公开数据中存在[对应原创仓库]({url})。' if url else '当前公开范围内未发现对应仓库。'
        else:
            detail = f'{md(a["description"])}；当前值 {proof["evidence"]["observed"]}，门槛 {proof["evidence"]["threshold"]}。'
        out.append(f'- **{md(a["title"])}** · {"已解锁" if proof["unlocked"] else "待解锁"} — {detail}')
    out.extend(['', '</details>', '', '成就是自定义作品里程碑；项目存在、星标和语言规则可自动核验，不代表 GitHub 官方成就或项目质量评定。'])
    return '\n'.join(out)

# Known public dependency names only; no code execution and no arbitrary URLs.
DEPENDENCIES = {
    'react': 'React', 'electron': 'Electron', 'vite': 'Vite', 'next': 'Next.js',
    'vue': 'Vue', 'svelte': 'Svelte', 'express': 'Express', 'three': 'Three.js',
    'tailwindcss': 'Tailwind CSS', 'fastapi': 'FastAPI', 'flask': 'Flask',
    'django': 'Django', 'numpy': 'NumPy', 'pandas': 'pandas', 'matplotlib': 'Matplotlib',
    'pygame': 'Pygame', 'pillow': 'Pillow', 'scipy': 'SciPy', 'torch': 'PyTorch',
    'transformers': 'Transformers', 'tauri': 'Tauri', 'tokio': 'Tokio', 'serde': 'Serde',
}
MANIFESTS = {'package.json', 'pyproject.toml', 'requirements.txt', 'Cargo.toml'}

def public_json(url, opener=urlopen, limit=1_000_000):
    req = Request(url, headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'Roy-Public-Profile-Sync', 'X-GitHub-Api-Version': '2026-03-10'})
    with opener(req, timeout=20) as response:
        if response.status != 200 or response.geturl() != url: raise ValueError('Unexpected public manifest API response')
        raw = response.read(limit+1)
        if len(raw) > limit: raise ValueError('Public manifest response too large')
        return json.loads(raw)

def dependency_names(path, source):
    names = []
    if path == 'package.json':
        data = json.loads(source)
        for field in ('dependencies', 'devDependencies', 'peerDependencies', 'optionalDependencies'):
            deps = data.get(field, {})
            if not isinstance(deps, dict): raise ValueError('Invalid dependency map')
            names.extend(deps)
    elif path == 'pyproject.toml':
        data = tomllib.loads(source)
        deps = data.get('project', {}).get('dependencies', [])
        groups = data.get('project', {}).get('optional-dependencies', {})
        if not isinstance(deps, list) or not isinstance(groups, dict): raise ValueError('Invalid Python dependencies')
        names.extend(deps)
        for group in groups.values():
            if not isinstance(group, list): raise ValueError('Invalid Python optional dependencies')
            names.extend(group)
        names.extend(data.get('tool', {}).get('poetry', {}).get('dependencies', {}))
    elif path == 'Cargo.toml':
        data = tomllib.loads(source)
        for field in ('dependencies', 'dev-dependencies', 'build-dependencies'):
            names.extend(data.get(field, {}))
    else:
        names.extend(line.strip() for line in source.splitlines() if line.strip() and not line.lstrip().startswith(('#', '-', 'http', 'git+')))
    found = set()
    for name in names:
        if not isinstance(name, str): raise ValueError('Dependency name must be text')
        match = re.match(r'^([A-Za-z0-9_.-]+)', name)
        if match and match[1].lower().replace('_', '-') in DEPENDENCIES:
            found.add(DEPENDENCIES[match[1].lower().replace('_', '-')])
    return sorted(found)

def manifest_repositories(config, repos):
    by_id = {r['id']: r for r in repos}
    recent = sorted(repos, key=lambda r: (max(r['pushed_at'] or '', r['created_at'] or ''), r['name']), reverse=True)[:config['recent_limit']]
    selected = {r['id']: r for r in recent}
    for featured in config['featured']:
        if featured['repository_id'] in by_id: selected[featured['repository_id']] = by_id[featured['repository_id']]
    return sorted(selected.values(), key=lambda r: r['id'])[:config['manifest_repository_limit']]

def fetch_manifests(config, repos, opener=urlopen):
    # Read at most eight public repository roots. Only recognized root manifests.
    evidence = []
    for repo in manifest_repositories(config, repos):
        base = 'https://api.github.com/repos/'+repo['full_name']+'/contents'
        listing = public_json(base, opener)
        if not isinstance(listing, list): raise ValueError('Invalid public root listing')
        for item in sorted(listing, key=lambda x: x.get('name', '')):
            if item.get('type') != 'file' or item.get('path') != item.get('name') or item.get('name') not in MANIFESTS: continue
            name = item['name']
            blob = public_json(base+'/'+name, opener)
            if blob.get('type') != 'file' or blob.get('path') != name or blob.get('encoding') != 'base64': raise ValueError('Invalid public manifest object')
            source = base64.b64decode(blob['content'], validate=False)
            if len(source) > 512_000: raise ValueError('Manifest source too large')
            labels = dependency_names(name, source.decode('utf-8'))
            if labels:
                evidence.append({'repository_id': repo['id'], 'path': name, 'url': repo['url']+'/blob/HEAD/'+name, 'source_sha': plain(blob.get('sha'), 64), 'technologies': labels})
    return evidence

def normalize_manifest_evidence(evidence, repos):
    by_id = {r['id']: r for r in repos}
    out = []
    for item in evidence:
        if item.get('repository_id') not in by_id or item.get('path') not in MANIFESTS: raise ValueError('Manifest evidence is outside public scope')
        if not isinstance(item.get('technologies'), list) or not all(x in DEPENDENCIES.values() for x in item['technologies']): raise ValueError('Invalid technology evidence')
        repo = by_id[item['repository_id']]
        out.append({'repository_id': repo['id'], 'path': item['path'], 'url': repo['url']+'/blob/HEAD/'+item['path'], 'source_sha': plain(item.get('source_sha'), 64), 'technologies': sorted(set(item['technologies']))})
    return sorted(out, key=lambda x: (x['repository_id'], x['path']))

def technology_slug(label):
    return hashlib.sha256(label.encode()).hexdigest()[:12]

def technology_svg(label, dependency=False, icon='terminal'):
    width = max(90, 44+len(label)*8)
    color = COLORS['rose'] if dependency else COLORS['mint']
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="28" viewBox="0 0 {width} 28" role="img" aria-label="{html.escape(label, quote=True)}"><path d="M4 1H{width-4}V4H{width-1}V24H{width-4}V27H4V24H1V4H4Z" fill="#18253a" stroke="#f4d58d"/>'+art_image(icon, 5, 3, 24, 22)+f'<text x="33" y="18.5" font-family="ui-monospace,SFMono-Regular,monospace" font-size="12" font-weight="600" fill="{color}">{html.escape(label)}</text></svg>\n'

def techstack_section(stats, manifests):
    languages = list(stats['primary_languages'])
    frameworks = sorted({name for item in manifests for name in item['technologies']})
    out = ['### Code equipment · 代码装备', '', '<p>']
    for label in languages+frameworks:
        out.append(f'  <img src="assets/generated/tech-{technology_slug(label)}.svg" alt="项目使用：{html.escape(label, quote=True)}" height="28">')
    out.extend(['</p>', '', '**主要语言** — '+(' · '.join(f'{md(k)}（{v} 个仓库）' for k, v in stats['primary_languages'].items()) or '暂无数据'), '', '**公开依赖** — '+(' · '.join(md(x) for x in frameworks) or '当前检查的根目录清单未匹配到已收录技术。'), '', '<details>', '<summary><b>Equipment notes · 数据来源</b></summary>', '', '语言取 GitHub 公开仓库的主要语言字段；依赖取最近更新与精选项目的根目录清单（最多 8 个仓库）。不表示熟练度，也不穷尽项目全部技术。', ''])
    for item in manifests:
        out.append(f'- [{md(item["url"].split("/blob/")[0].split("/")[-1])} / {md(item["path"])}]({item["url"]}) — '+ ' · '.join(md(x) for x in item['technologies']))
    out.extend(['', '</details>'])
    return '\n'.join(out)

def replace_region(text, name, content):
    begin = f'<!-- PROFILE-SYNC:{name}:START -->'
    end = f'<!-- PROFILE-SYNC:{name}:END -->'
    if text.count(begin) != 1 or text.count(end) != 1: raise ValueError(f'Missing or duplicate {name} markers')
    start, stop = text.index(begin), text.index(end)
    if stop < start: raise ValueError('Markers are reversed')
    return text[:start+len(begin)]+'\n'+content+'\n'+text[stop:]

def run(root, raw=None, now=None, opener=urlopen, manifest_evidence=None, extra_public=None):
    root = Path(root).resolve()
    config = load_config(root/'config/profile.json')
    readme = (root/'README.md').read_text()
    # Validate every marker before network access or mutation.
    for marker in MARKERS: replace_region(readme, marker, '')
    live = raw is None
    observed = now or datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
    if live: raw = fetch_public(config['owner'], opener=opener)
    if not isinstance(raw, list): raise ValueError('Repository source must be a list')
    repos = normalize_public(raw, config)
    stats = metrics(repos)
    activity = extra_public or (fetch_extended(config, observed, opener) if live else offline_activity(observed))
    all_config = dict(config, exclude_forks=False, exclude_archived=False)
    stats.update({'public_owned_count':len(normalize_public(raw, all_config)), 'followers':activity['followers'], 'following':activity['following'], 'updated_projects_30d':sum(activity['window_start'] <= (r['pushed_at'] or '')[:10] <= activity['window_end'] for r in repos)})
    manifests = normalize_manifest_evidence(fetch_manifests(config, repos, opener) if manifest_evidence is None else manifest_evidence, repos)
    evidence = resolve_achievements(config, repos, stats)
    unlocked = sum(p['unlocked'] for p in evidence)
    content = {'schema_version': 1, 'owner': config['owner'], 'owner_id': config['owner_id'], 'scope': 'public-owned-nonfork-nonarchived-excluding-profile', 'repositories': repos, 'metrics': stats, 'public_manifest_evidence': manifests, 'public_activity': activity}
    digest = hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    snapshot_path = root/'data/public-snapshot.json'
    old = json.loads(snapshot_path.read_text()) if snapshot_path.exists() else {}
    stamp = old.get('observed_at') if old.get('data_sha256') == digest else None
    stamp = stamp or now or datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
    content.update({'observed_at': stamp, 'data_sha256': digest, 'source_url': f'https://api.github.com/users/{config["owner"]}/repos'})
    new_readme = replace_region(readme, 'projects', projects_section(config, repos, stats, activity))
    new_readme = replace_region(new_readme, 'achievements', achievements_section(config, evidence, stats))
    new_readme = replace_region(new_readme, 'techstack', techstack_section(stats, manifests))
    encode = lambda value: json.dumps(value, ensure_ascii=False, indent=2)+'\n'
    files = {'README.md': new_readme, 'data/public-snapshot.json': encode(content), 'data/achievement-evidence.json': encode({'schema_version': 1, 'snapshot_sha256': digest, 'achievements': evidence}), 'assets/generated/status.svg': status_svg(stats, unlocked), 'assets/generated/languages.svg': languages_svg(stats), 'assets/generated/activity.svg': activity_svg(stats, activity)}
    frameworks = {name for item in manifests for name in item['technologies']}
    for label in [*stats['primary_languages'], *sorted(frameworks)]:
        files[f'assets/generated/tech-{technology_slug(label)}.svg'] = technology_svg(label, dependency=label in frameworks)
    for a, proof in zip(config['achievements'], evidence):
        if proof['unlocked']: files[f'assets/generated/achievement-{a["id"]}.svg'] = achievement_svg(a)
    old_manifest_path = root/'data/generated-files.json'
    old_paths = json.loads(old_manifest_path.read_text()).get('files', []) if old_manifest_path.exists() else []
    for relative in old_paths:
        if relative not in GENERATED_FILES and not re.fullmatch(r'assets/generated/(?:status|languages|activity|tech-[a-f0-9]{12}|achievement-[a-z0-9-]+)\.svg', relative): raise ValueError('Unsafe generated-file manifest path')
    files['data/generated-files.json'] = encode({'schema_version': 1, 'files': sorted([*files, 'data/generated-files.json'])})
    # Everything is fetched/validated/rendered before changing local files.
    changed = []
    for relative, value in files.items():
        path = root/relative
        if not path.exists() or path.read_text() != value:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_name(path.name+'.sync-tmp')
            temp.write_text(value)
            temp.replace(path)
            changed.append(relative)
    for relative in old_paths:
        if relative not in files:
            path = root/relative
            if path.exists(): path.unlink(); changed.append(relative)
    return {'project_count': stats['project_count'], 'total_stars': stats['total_stars'], 'language_count': stats['language_count'], 'unlocked_achievements': unlocked, 'changed_files': sorted(changed), 'data_sha256': digest, 'manifest_sources': len(manifests)}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--fixture', type=Path, help='Offline public repository JSON list; dependency evidence is empty in this mode')
    args = parser.parse_args()
    try:
        raw = json.loads(args.fixture.read_text()) if args.fixture else None
        print(json.dumps(run(args.root, raw=raw, manifest_evidence=[] if args.fixture else None), ensure_ascii=False))
    except Exception as error:
        print('Profile sync failed: '+type(error).__name__+': '+str(error), file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__': raise SystemExit(main())
