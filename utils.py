import sre_parse as sp
from typing import Any

import re2 as re
from parse_discord import parse


def render_pattern(pattern, flags):
    return f"/{pattern}/{flags}"

sp: Any
def _regex_min(t):
    n = 0
    for rtype, args in t:
        if rtype in (sp.LITERAL, sp.CATEGORY, sp.RANGE, sp.ANY):
            n += 1
        elif rtype == sp.MAX_REPEAT:
            n += args[0] * _regex_min(args[2])
        elif rtype == sp.IN:
            n += sum(_regex_min([x]) for x in args)
        elif rtype == sp.BRANCH:
            n += min(map(_regex_min, args[1]))
        elif rtype == sp.SUBPATTERN:
            n += _regex_min(args[3])
    return n

def regex_min(pattern):
    return _regex_min(sp.parse(pattern))

regex_cache = {}
def matches(regex, content, flags):
    key = (regex, flags)

    try:
        o = regex_cache[key]
    except KeyError:
        options = re.Options()
        if "i" in flags:
            options.case_sensitive = False
        if "s" in flags:
            options.dot_nl = True
        options.never_capture = True
        o = re.compile(regex, options)
        regex_cache[key] = o

    return o.search(content)

def english_list(l, merger="and"):
    if len(l) == 1:
        return f"{l[0]}"
    elif len(l) == 2:
        return f"{l[0]} {merger} {l[1]}"
    else:
        return f"{', '.join(l[:-1])}, {merger} {l[-1]}"

def sanitize_markdown(text):
    return str(parse(text))
