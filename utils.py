import sre_parse as sp
from typing import Any

import re2 as re


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


class View:
    def __init__(self, text):
        self.text = text
        self.idx = 0
        self.out = []
        self.stack = []

    def read_some(self, char):
        count = 0
        while True:
            self.idx += 1
            count += 1
            if self.eof() or self.peek() != char:
                break
        self.out.append(char * count)
        return count

    def read_normal(self):
        s = ""
        c = self.peek()
        while True:
            s += c
            self.idx += 1
            if self.eof():
                break
            c = self.peek()
            if c in ("*", "_", "|", "~", "`"):
                break
        self.out.append(s)

    def peek(self):
        return self.text[self.idx]

    def eof(self):
        return self.idx >= len(self.text)

    def do(self, t):
        if self.stack and self.stack[-1][0] == t:
            self.stack.pop()
        else:
            self.stack.append((t, len(self.out)-1))


def sanitize_markdown(text):
    view = View(text)

    while not view.eof():
        c = view.peek()
        if c == "|":
            if view.read_some("|") % 5 >= 2:
                view.do("||")
        elif c == "~":
            if view.read_some("~") % 5 >= 2:
                view.do("~~")
        elif c == "*":
            n = view.read_some("*")
            if n == 3 or (n > 3 and n % 2 == 1):
                view.do("***")
            elif n == 2:
                view.do("**")
            elif n == 1:
                view.do("*")
        elif c == "_":
            view.read_some("_")
            # not perfect but I don't give a shit
            view.do("_")
        elif c == "`":
            n = view.read_some("`") % 7
            if n == 1:
                view.do("`")
            elif n == 2:
                view.do("``")
            else:
                view.do("```")
        else:
            view.read_normal()

    for _, idx in view.stack:
        b = "\\"
        view.out[idx] = f"\\{b.join(view.out[idx])}"

    return "".join(view.out)
