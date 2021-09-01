import sre_parse as sp


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
