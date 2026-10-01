"""Shared ranking for cached and provider team results."""
from __future__ import annotations

import re
from difflib import SequenceMatcher

LIMIT = 15


def normalize_query(value: str) -> str:
    return ' '.join(re.findall(r'\w+', value.casefold()))


def match_rank(name: str, query: str) -> tuple | None:
    name = normalize_query(name)
    query = normalize_query(query)
    if not query:
        return None
    words, tokens = name.split(), query.split()
    if name == query:
        return (0, 0, name)
    if name.startswith(query):
        return (1, len(name), name)
    # Each abbreviated query word must match a distinct name word, in order.
    pos = 0
    for token in tokens:
        while pos < len(words) and not words[pos].startswith(token):
            pos += 1
        if pos == len(words):
            break
        pos += 1
    else:
        return (2, len(name), name)
    if query in name:
        return (3, len(name), name)
    if len(query) < 3:
        return None
    # Whole words only: avoid suggesting unrelated names for tiny fragments.
    if len(tokens) > 1:
        scores = []
        pos = 0
        for token in tokens:
            candidates = [(SequenceMatcher(None, token, word).ratio(), index)
                          for index, word in enumerate(words[pos:], start=pos)]
            if not candidates:
                return None
            score, index = max(candidates)
            if score < 0.75:
                return None
            scores.append(score)
            pos = index + 1
        similarity = sum(scores) / len(scores)
    else:
        similarity = max(
            SequenceMatcher(None, query, name).ratio(),
            *(SequenceMatcher(None, query, word).ratio() for word in words),
        )
    if similarity >= 0.75:
        return (4, -similarity, len(name), name)
    return None


def rank_teams(teams, query: str, limit: int = LIMIT) -> list[dict]:
    ranked = []
    seen = set()
    for team in teams:
        team = dict(team)
        key = str(team.get('external_id') or team['id'])
        rank = match_rank(team['name'], query)
        if key not in seen and rank is not None:
            seen.add(key)
            ranked.append((rank, key, team))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [team for _, _, team in ranked[:limit]]
