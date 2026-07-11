#!/usr/bin/env python3
"""
Update GitHub Profile README with dynamic content:
- Quote of the Day (from api.quotable.io)
- GitHub Stats (repos, stars, commits, followers, LOC)

Author: BroKarim
"""

import os
import sys
import json
import time
import hashlib
import requests
import datetime
from dateutil import relativedelta
from lxml import etree


# ============================================================
# Configuration
# ============================================================
HEADERS = {'Authorization': f'token {os.environ["ACCESS_TOKEN"]}'}
USER_NAME = os.environ.get('USER_NAME', 'BroKarim')

QUERY_COUNT = {
    'user_getter': 0, 'follower_getter': 0,
    'graph_repos_stars': 0, 'recursive_loc': 0,
    'graph_commits': 0, 'loc_query': 0
}

# SVG element IDs to update
SVG_ELEMENTS = {
    'age_data': 'age_data',
    'age_data_dots': 'age_data_dots',
    'repo_data': 'repo_data',
    'repo_data_dots': 'repo_data_dots',
    'star_data': 'star_data',
    'star_data_dots': 'star_data_dots',
    'commit_data': 'commit_data',
    'commit_data_dots': 'commit_data_dots',
    'follower_data': 'follower_data',
    'follower_data_dots': 'follower_data_dots',
    'quote_text': 'quote_text',
    'quote_author': 'quote_author',
}


# ============================================================
# Utility Functions
# ============================================================
def format_plural(unit):
    return 's' if unit != 1 else ''


def daily_readme(birthday):
    diff = relativedelta.relativedelta(datetime.datetime.now(datetime.timezone.utc), birthday)
    return f"{diff.years} year{format_plural(diff.years)}, {diff.months} month{format_plural(diff.months)}, {diff.days} day{format_plural(diff.days)}"


def query_count(func_name):
    QUERY_COUNT[func_name] += 1


def graphql_request(func_name, query, variables):
    query_count(func_name)
    resp = requests.post(
        'https://api.github.com/graphql',
        json={'query': query, 'variables': variables},
        headers=HEADERS,
        timeout=30
    )
    if resp.status_code == 200:
        return resp.json()
    raise Exception(f"{func_name} failed: {resp.status_code} - {resp.text}")


# ============================================================
# Quote of the Day (api.quotable.io)
# ============================================================
def fetch_quote():
    """Fetch a random inspirational quote"""
    urls = [
        "https://zenquotes.io/api/random",
        "https://api.quotable.io/random?tags=technology|inspirational|programming",
    ]
    for url in urls:
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list) and len(data) > 0:
                    return data[0].get('q', ''), data[0].get('a', 'Unknown')
                return data.get('content', ''), data.get('author', 'Unknown')
        except Exception:
            continue
    return "Code is poetry written in logic.", "Anonymous"


# ============================================================
# GitHub GraphQL Queries
# ============================================================
def get_user_id():
    query = '''
    query($login: String!) {
        user(login: $login) {
            id
            createdAt
        }
    }'''
    data = graphql_request('user_getter', query, {'login': USER_NAME})
    return data['data']['user']['id'], data['data']['user']['createdAt']


def get_followers():
    query = '''
    query($login: String!) {
        user(login: $login) {
            followers { totalCount }
        }
    }'''
    data = graphql_request('follower_getter', query, {'login': USER_NAME})
    return data['data']['user']['followers']['totalCount']


def get_repos_and_stars():
    """Get total repos and stars for owned repositories"""
    query = '''
    query($login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 100, after: $cursor, ownerAffiliations: [OWNER]) {
                totalCount
                edges {
                    node {
                        nameWithOwner
                        stargazers { totalCount }
                    }
                }
                pageInfo { endCursor, hasNextPage }
            }
        }
    }'''
    total_repos = 0
    total_stars = 0
    cursor = None
    while True:
        data = graphql_request('graph_repos_stars', query, {'login': USER_NAME, 'cursor': cursor})
        repos = data['data']['user']['repositories']
        total_repos = repos['totalCount']
        for edge in repos['edges']:
            total_stars += edge['node']['stargazers']['totalCount']
        if not repos['pageInfo']['hasNextPage']:
            break
        cursor = repos['pageInfo']['endCursor']
    return total_repos, total_stars


def get_total_commits(start_date, end_date):
    query = '''
    query($login: String!, $start: DateTime!, $end: DateTime!) {
        user(login: $login) {
            contributionsCollection(from: $start, to: $end) {
                contributionCalendar { totalContributions }
            }
        }
    }'''
    data = graphql_request('graph_commits', query, {
        'login': USER_NAME,
        'start': start_date,
        'end': end_date
    })
    return data['data']['user']['contributionsCollection']['contributionCalendar']['totalContributions']


def get_loc(owner_affiliation):
    """Compute total lines of code (additions - deletions) across repositories"""
    query = '''
    query($login: String!, $cursor: String, $affiliation: [RepositoryAffiliation]) {
        user(login: $login) {
            repositories(first: 60, after: $cursor, ownerAffiliations: $affiliation) {
                edges {
                    node {
                        nameWithOwner
                        defaultBranchRef {
                            target {
                                ... on Commit {
                                    history { totalCount }
                                }
                            }
                        }
                    }
                }
                pageInfo { endCursor, hasNextPage }
            }
        }
    }'''
    all_edges = []
    cursor = None
    while True:
        data = graphql_request('loc_query', query, {
            'login': USER_NAME, 'cursor': cursor, 'affiliation': owner_affiliation
        })
        repos = data['data']['user']['repositories']
        all_edges.extend(repos['edges'])
        if not repos['pageInfo']['hasNextPage']:
            break
        cursor = repos['pageInfo']['endCursor']

    # Cache file
    cache_dir = 'cache'
    os.makedirs(cache_dir, exist_ok=True)
    cache_file = os.path.join(cache_dir, hashlib.sha256(USER_NAME.encode()).hexdigest() + '.txt')

    # Load existing cache
    cached = {}
    if os.path.exists(cache_file):
        with open(cache_file, 'r') as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) >= 5:
                    cached[parts[0]] = {'commits': int(parts[1]), 'add': int(parts[3]), 'del': int(parts[4])}

    total_add = 0
    total_del = 0
    total_commits = 0

    for edge in all_edges:
        repo = edge['node']
        name = repo['nameWithOwner']
        repo_hash = hashlib.sha256(name.encode()).hexdigest()
        commit_count = repo['defaultBranchRef']['target']['history']['totalCount'] if repo['defaultBranchRef'] else 0

        cached_entry = cached.get(repo_hash)
        if cached_entry and cached_entry['commits'] == commit_count:
            total_add += cached_entry['add']
            total_del += cached_entry['del']
            total_commits += cached_entry['commits']
            continue

        # Fetch LOC for this repo
        add, delete, my_commits = fetch_repo_loc(name, commit_count)
        total_add += add
        total_del += delete
        total_commits += my_commits

        # Update cache
        cached[repo_hash] = {'commits': commit_count, 'add': add, 'del': delete}

    # Save cache
    with open(cache_file, 'w') as f:
        for h, v in cached.items():
            f.write(f"{h} {v['commits']} {v.get('my_commits', 0)} {v['add']} {v['del']}\n")

    return total_add, total_del, total_add - total_del, total_commits


def fetch_repo_loc(name_with_owner, total_commits):
    """Fetch LOC for a single repository by iterating through commit history"""
    owner, repo_name = name_with_owner.split('/')
    query = '''
    query($owner: String!, $repo: String!, $cursor: String) {
        repository(owner: $owner, name: $repo) {
            defaultBranchRef {
                target {
                    ... on Commit {
                        history(first: 100, after: $cursor) {
                            totalCount
                            edges {
                                node {
                                    author { user { id } }
                                    additions
                                    deletions
                                }
                            }
                            pageInfo { endCursor, hasNextPage }
                        }
                    }
                }
            }
        }
    }'''

    # Get user ID for filtering
    user_id = get_user_id()[0]

    total_add = 0
    total_del = 0
    my_commits = 0
    cursor = None

    while True:
        data = graphql_request('recursive_loc', query, {
            'owner': owner, 'repo': repo_name, 'cursor': cursor
        })
        history = data['data']['repository']['defaultBranchRef']['target']['history']
        for edge in history['edges']:
            node = edge['node']
            if node['author']['user'] and node['author']['user']['id'] == user_id:
                my_commits += 1
                total_add += node['additions']
                total_del += node['deletions']
        if not history['pageInfo']['hasNextPage']:
            break
        cursor = history['pageInfo']['endCursor']

    return total_add, total_del, my_commits


# ============================================================
# SVG Update
# ============================================================
def justify_format(root, element_id, new_text, length=0):
    if isinstance(new_text, int):
        new_text = f"{new_text:,}"
    new_text = str(new_text)

    elem = root.find(f".//*[@id='{element_id}']")
    if elem is not None:
        elem.text = new_text

    dots_id = f"{element_id}_dots"
    dots_elem = root.find(f".//*[@id='{dots_id}']")
    if dots_elem is not None:
        just_len = max(0, length - len(new_text))
        if just_len <= 2:
            dot_map = {0: '', 1: ' ', 2: '. '}
            dots_elem.text = dot_map[just_len]
        else:
            dots_elem.text = ' ' + ('.' * just_len) + ' '


def update_quote(root, quote_text, author_name):
    """Update quote text with word wrapping, shifting subsequent elements down."""
    max_chars = 54
    line_h = 16

    quote_text_elem = root.find(".//*[@id='quote_text']")
    quote_author_elem = root.find(".//*[@id='quote_author']")
    if quote_text_elem is None:
        return

    parent = quote_text_elem.getparent()
    # Get SVG namespace from root
    tag = root.tag
    ns = tag[tag.index('{'):tag.index('}') + 1] if '}' in tag else ''

    children = list(parent)
    text_idx = children.index(quote_text_elem)
    start_y = int(quote_text_elem.get('y'))

    # Word wrap
    words = quote_text.split()
    lines = []
    cur = ''
    for w in words:
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= max_chars:
            cur += ' ' + w
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    num = len(lines)

    # Remove all old quote elements (from text_idx up to Contact header)
    for child in children[text_idx:]:
        if child.get('x') == '390':
            break
        parent.remove(child)

    # Insert wrapped lines + author (no shifting — Contact stays at original y)
    tspan_tag = f'{ns}tspan'
    for i, line in enumerate(lines):
        attrs = {'x': '436', 'y': str(start_y + i * line_h), 'class': 'value'}
        if i == 0:
            attrs['id'] = 'quote_text'
        el = etree.Element(tspan_tag, attrib=attrs)
        el.text = line
        parent.insert(text_idx + i, el)

    attrs = {'x': '436', 'y': str(start_y + num * line_h), 'class': 'value', 'id': 'quote_author'}
    el = etree.Element(tspan_tag, attrib=attrs)
    el.text = f"~ {author_name}"
    parent.insert(text_idx + num, el)


def update_svg(svg_path, stats, quote):
    tree = etree.parse(svg_path)
    root = tree.getroot()

    justify_format(root, 'age_data', stats['age'], 0)
    justify_format(root, 'repo_data', stats['repos'], 6)
    justify_format(root, 'star_data', stats['stars'], 14)
    justify_format(root, 'commit_data', stats['commits'], 22)
    justify_format(root, 'follower_data', stats['followers'], 10)

    # Quote elements with word wrapping
    update_quote(root, quote[0], quote[1])

    tree.write(svg_path, encoding='utf-8', xml_declaration=True)
    print(f"Updated {svg_path}")


# ============================================================
# Main
# ============================================================
def main():
    print("=== GitHub Profile Updater ===")
    print(f"User: {USER_NAME}")

    # Fetch quote
    print("Fetching quote...")
    quote = fetch_quote()
    print(f"Quote: \"{quote[0]}\" — {quote[1]}")

    # Fetch GitHub stats
    print("Fetching GitHub stats...")
    user_id, created_at = get_user_id()

    # Account age
    acc_date = datetime.datetime.fromisoformat(created_at.replace('Z', '+00:00'))
    age = daily_readme(acc_date)

    # Repos & Stars
    repos, stars = get_repos_and_stars()

    # Commits (last year)
    end_date = datetime.datetime.now(datetime.timezone.utc).isoformat()
    start_date = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=365)).isoformat()
    commits = get_total_commits(start_date, end_date)

    # Followers
    followers = get_followers()

    stats = {
        'age': age,
        'repos': repos,
        'stars': stars,
        'commits': commits,
        'followers': followers,
    }

    print(f"\nStats:")
    for k, v in stats.items():
        print(f"  {k}: {v}")

    # Update SVGs
    print("\nUpdating SVGs...")
    update_svg('assets/dark_mode.svg', stats, quote)
    update_svg('assets/light_mode.svg', stats, quote)

    print("\n=== Done ===")
    print(f"Total GraphQL queries: {sum(QUERY_COUNT.values())}")
    for k, v in QUERY_COUNT.items():
        if v:
            print(f"  {k}: {v}")


if __name__ == '__main__':
    if 'ACCESS_TOKEN' not in os.environ:
        print("ERROR: ACCESS_TOKEN environment variable required")
        sys.exit(1)
    main()
