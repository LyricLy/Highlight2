from typing import Any

import re2 as re
from parse_discord import *


def render_pattern(pattern, flags):
    return f"/{pattern}/{flags}"    

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

def cut(text, to):
    return text[:to], to - len(text)

def truncate(markup, to):
    for i, node in enumerate(markup.nodes):
        if to <= 0:
            del markup.nodes[i:]
            break
        match node:
            case Text(c):
                node.text, to = cut(c, to)
            case Codeblock(c) | InlineCode(c):
                node.content, to = cut(c, to)
            case _:
                for inner in node.inners:
                    to = truncate(inner, to)
    return to

def display_message(text):
    m = parse(text)
    truncated = truncate(m, 700) < 0
    return f"{m}{'...'*truncated}"
