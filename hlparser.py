import unicodedata

import re2 as re
import parse_discord
import discord
from discord.ext import commands

from utils import matches


class LexFailure(ValueError):
    pass

quotes = {
    '"': '"',
    "'": "'",
    "‘": "’",
    "‚": "‛",
    "“": "”",
    "„": "‟",
    "⹂": "⹂",
    "「": "」",
    "『": "』",
    "〝": "〞",
    "﹁": "﹂",
    "﹃": "﹄",
    "＂": "＂",
    "｢": "｣",
    "«": "»",
    "‹": "›",
    "《": "》",
    "〈": "〉",
}

class StringView:
    def __init__(self, string, bot):
        self.string = string
        self.idx = 0
        self.bot = bot

    @property
    def is_eof(self):
        return self.idx >= len(self.string)

    def peek(self):
        return self.string[self.idx] if not self.is_eof else ""

    def consume(self, n=1):
        self.idx += n

    def consume_literal(self, text):
        if self.string[self.idx:self.idx+len(text)] == text:
            self.consume(len(text))
            return True
        return False

    def get_quoted_word(self):
        v = commands.view.StringView(self.string)  # type: ignore
        v.index = self.idx
        v.previous = self.idx
        try:
            w = v.get_quoted_word()
        except commands.errors.UnexpectedQuoteError:
            self.idx = v.index
            self.fail("unexpected quote inside unquoted word")
        except commands.errors.InvalidEndOfQuotedStringError:
            self.idx = v.index
            self.fail("expected EOF or whitespace after quoted word")
        except commands.errors.ExpectedClosingQuoteError:
            self.idx = v.index
            self.fail("reached EOF while parsing a quoted word")
        else:
            self.idx = v.index
            return w

    def fail(self, msg, help_msg=None):
        e = f"error: {msg}\n"
        n = 0
        for line in self.string.splitlines():
            e += f"  | {line}\n"
            if n + len(line) > self.idx:
                e += "  | " + " "*(self.idx-n) + "^\n"  # type: ignore
                n = -float('inf')
            n += len(line)
        e += f"help: {help_msg}" if help_msg else ""
        raise LexFailure(e)

    def skip_ws(self):
        while self.peek().isspace() or self.peek() == "`":
            self.consume()

    def lex_rule(self, guild):
        if self.consume_literal("!") or self.consume_literal("-"):
            self.skip_ws()
            negate = True
        else:
            negate = False

        if self.peek() in quotes:
            end = quotes[self.peek()]
            self.consume()
            s = ""
            while self.peek() != end:
                if self.is_eof:
                    self.fail("reached EOF while parsing quoted string")
                c = self.peek()
                if c == "\\":
                    self.consume()
                    if self.peek() == "\\":
                        s += "\\"
                        self.consume()
                    elif self.peek() == end:
                        s += end
                        self.consume()
                    else:
                        self.fail("invalid escape", "you can only escape backslashes and ending quotes")
                else:
                    s += c
                    self.consume()
            self.consume()

            if not s:
                self.fail("string cannot be empty", "if you want to match any message, you don't need to provide a string condition")
            return {"type": "literal", "text": s, "negate": negate}
        elif self.peek() == "/":
            self.consume()
            p = ""
            escaping = False
            while True:
                if self.is_eof:
                    self.fail("reached EOF while parsing regular expression")
                c = self.peek()
                self.consume()
                if c == "/" and not escaping:
                    break
                p += c
                escaping = c == "\\"
            flags = ""
            while self.peek().isalpha():
                if self.peek() not in "is":
                    self.fail("invalid flag", "only `i` and `s` are supported")
                if self.peek() in flags:
                    self.fail("flag repeated")
                flags += self.peek()
                self.consume()

            try:
                re.compile(p)
            except re.error as e:
                self.fail(f"regex is invalid: {e.args[0].decode()}")

            if matches(p, "", ""):
                self.fail("regex should not match the empty string", "if you want to match any message, you don't need to provide a regex condition")

            return {"type": "regex", "regex": p, "flags": "".join(sorted(flags)), "negate": negate}
        elif self.consume_literal("+"):
            if negate:
                self.fail("reaction conditions cannot be negated")
            self.skip_ws()
            match parse_discord.parse(self.string[self.idx:]).nodes:
                case [(parse_discord.UnicodeEmoji() | parse_discord.CustomEmoji()) as e, *_]:
                    s = str(parse_discord.Markup([e]))
                case _:
                    self.fail("expected emoji")
            self.consume(len(s))
            return {"type": "react", "emoji": s, "negate": False}
        elif self.consume_literal("guild:") or self.consume_literal("server:"):
            w = self.get_quoted_word()
            guild = discord.utils.get(self.bot.guilds, name=w)
            if not guild:
                try:
                    guild = self.bot.get_guild(int(w))
                except ValueError:
                    guild = None
            if not guild:
                self.fail("unknown guild")
            return {"type": "guild", "id": guild.id, "negate": negate}
        elif self.consume_literal("channel:") or self.consume_literal("in:"):
            w = self.get_quoted_word()
            if m := re.fullmatch("<#([0-9]+)>", w):
                w = m.group(1)
            channel = discord.utils.get(guild.channels, name=w.removeprefix("#")) if guild else None
            if not channel:
                try:
                    channel = self.bot.get_channel(int(w))
                except ValueError:
                    channel = None
            if not channel:
                self.fail("unknown channel")
            return {"type": "channel", "id": channel.id, "negate": negate}
        elif self.consume_literal("author:") or self.consume_literal("from:") or self.consume_literal("user:"):
            w = self.get_quoted_word()
            if m := re.fullmatch("<@!?([0-9]+)>", w):
                w = m.group(1)
            user = guild.get_member_named(w) if guild else None
            if not user:
                try:
                    user = self.bot.get_user(int(w))
                except ValueError:
                    user = None
            if not user:
                self.fail("unknown user")
            return {"type": "author", "id": user.id, "negate": negate}
        elif self.consume_literal("noglobal"):
            return {"type": "noglobal"}
        elif self.consume_literal("bot"):
            return {"type": "bot", "negate": negate}
        else:
            self.fail(f"unknown start of token '{self.peek()}' ({unicodedata.name(self.peek()).title()})", "wrap literal strings in quotes and regular expressions in slashes")

def parse(text, ctx):
    view = StringView(text, ctx.bot)
    view.skip_ws()
    filters = []
    noglobal = False
    while not view.is_eof:
        rule = view.lex_rule(ctx.guild)
        if rule["type"] == "noglobal":
            noglobal = True
        else:
            filters.append(rule)
        view.skip_ws()
    return filters, noglobal
