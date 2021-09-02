import asyncio
import base64
import time
import json
import unicodedata
import os
from collections import defaultdict
from typing import Union

import re2 as re
import discord
from discord.ext import commands

from regex_min import regex_min


def render_pattern(pattern, flags):
    return f"/{pattern}/{flags}"


intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(
    command_prefix=commands.when_mentioned,
    description="A highlighting bot that DMs you when someone says something that matches a preconfigured set of criteria.",
    max_messages=None,
    allowed_mentions=discord.AllowedMentions(everyone=False),
    intents=intents,
)
message_cache = defaultdict(list)
last_active = defaultdict(float)

try:
    with open("config.json") as f:
        config = json.load(f)
except FileNotFoundError:
    config = {}

def get_user(member):
    return config.setdefault(str(member.id), {"highlights": []})

settings = {
    "before_time": ("delay-before", "Delay before", "Highlights don't work if you're active in the channel. "
                                                    "This is the amount of time it takes after your last activity before you're no longer considered active.", 30, int),
    "after_time": ("delay-after", "Delay after",
                   "The delay after a highlight is triggered before the DM is sent. If you're active in this time, the highlight is cancelled.", 10, int),
    "no_repeat": ("no-repeat", "Don't repeat", "Treat highlights as 'activity', so you won't be highlighted more than once in a row.", False, bool),
}

@bot.group(invoke_without_command=True, name="settings", aliases=["opt", "cfg", "config"])
async def _settings(ctx):
    user = get_user(ctx.author)
    embed = discord.Embed()
    for opt, (cmd_name, display_name, description, _, _) in settings.items():
        v = get_config(user, opt)
        embed.add_field(name=f"{display_name} (`cfg {cmd_name} {v}`)", value=f"Set to {v}.\n{description}", inline=False)
    await ctx.send(embed=embed)


for opt, (cmd_name, _, description, _, conv) in settings.items():
    def g(opt=opt):
        @_settings.command(name=cmd_name, brief=description, help=description)
        async def c(ctx, v: conv):  # type: ignore
            user = get_user(ctx.author)
            user[opt] = v
            save()
            await ctx.send("👍")
    g()

def get_config(user, v):
    return user.get(v, settings[v][3])

def save():
    with open("config.json.new", "w") as f:
        json.dump(config, f)
    os.replace("config.json.new", "config.json")

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

async def send_highlight(user, patterns, msg):
    # TODO: better embed
    timestamp = discord.utils.format_dt(msg.created_at, 't')  # type: ignore
    embed = discord.Embed(description=f"[{timestamp}] **{msg.author.display_name}**: {msg.content}")
    embed.add_field(name="\u200b", value=f"[Jump to message]({msg.jump_url})")

    pattern_string = english_list([repr(x) for x in patterns])
    highlights = "Highlight" if len(patterns) == 1 else "Highlights"
    try:
        await user.send(f'{highlights} {pattern_string} in **{msg.guild.name}**/{msg.channel.mention} by {msg.author.mention} ({msg.author})', embed=embed)
    except discord.HTTPException:
        pass

def merge_filters(filters):
    rules = defaultdict(list)
    out_filters = []
    for f in filters:
        if f["type"] in ("guild", "channel", "author"):
            if not f["negate"]:
                rules[f["type"]].append(f['id'])
            else:
                out_filters.append({"type": f["type"], "ids": [f["id"]], "negate": True})
        else:
            out_filters.append(f)
    for k, v in rules.items():
        out_filters.append({"type": k, "ids": list(set(v)), "negate": False})
    return out_filters


@bot.event
async def on_raw_reaction_add(payload):
    last_active[(payload.channel_id, payload.user_id)]

@bot.event
async def on_message_edit(before, after):
    last_active[(after.channel.id, after.author.id)] = time.time()

@bot.event
async def on_typing(channel, user, when):
    last_active[(channel.id, user.id)] = when.timestamp()

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    await bot.process_commands(message)

    last_active[(message.channel.id, message.author.id)] = time.time()

    users_to_highlight = defaultdict(list)
    for id, user in config.items():
        if not message.guild:
            continue
        user_obj = message.guild.get_member(int(id))
        if not user_obj:
            continue

        if (not message.guild or not message.channel.permissions_for(user_obj).read_messages or not user.get("enabled", True)
         or message.author.id in (blocked := user.get("blocked", [])) or message.channel.id in blocked):
            continue

        start_last_active = last_active.get((message.channel.id, int(id)), 0)
        if time.time()-start_last_active < user.get("before_time", 30):
            continue

        successes = []
        global_result = True
        for highlight in user["highlights"]:
            is_global = highlight["name"] == "global"
            for f in merge_filters(highlight["filters"]):
                t = f["type"]
                if t == "literal":
                    x = matches(r"\b" + re.escape(f['text']) + r"\b", message.content, "")
                elif t == "regex":
                    x = matches(f['pattern'], message.content, f['flags'])
                elif t == "guild":
                    x = message.guild.id in f['ids']
                elif t == "channel":
                    x = message.channel.id in f['ids']
                elif t == "author":
                    x = message.author.id in f['ids']
                else:
                    assert False
                if bool(x) != (not f["negate"]):
                    break
            else:
                if not is_global:
                    successes.append(highlight)
                continue
            if is_global:
                global_result = False

        successes = [x["name"] for x in successes if global_result or x["noglobal"]]

        if successes:
            await asyncio.sleep(user.get("after_time", 10))
            if last_active.get((message.channel.id, int(id)), 0) > start_last_active:
                # they spoke during the sleep
                continue

            if get_config(user, "no_repeat"):
                last_active[(message.channel.id, message.author.id)] = time.time()
            await send_highlight(user_obj, successes, message)


@bot.command(aliases=["list"])
async def show(ctx):
    """List all of your highlight triggers."""

    user = config.get(str(ctx.author.id), {"highlights": []})

    embed = discord.Embed(title="Your highlight triggers" + " are disabled"*(not user.get("enabled", True)), description="")
    embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.avatar)

    for highlight in user["highlights"]:
        n = []
        for f in merge_filters(highlight["filters"]):
            t = f["type"]
            d = " not" * f['negate']
            if t == "literal":
                n.append(f"**does{d}** contain {f['text']!r}")
            elif t == "regex":
                n.append(f"**does{d}** match {render_pattern(f['regex'], f['flags'])}")
            elif t == "guild":
                gss = []
                for id in f['ids']:
                    g = bot.get_guild(id)
                    gs = f"server {g.name}" if g else f"<unknown server {id}>"
                    gss.append(gs)
                n.append(f"**is{d}** in {english_list(gss, 'or')}")
            elif t == "channel":
                cs = [f"<#{id}>" for id in f['ids']]
                n.append(f"**is{d}** in {english_list(cs, 'or')}")
            elif t == "author":
                uss = []
                for id in f['ids']:
                    u = bot.get_user(id)
                    us = f"<@{u.id}> ({u})" if u else f"<@{id}>"                                                                                                                                                                                 ; us = us[:24] + us[25] + us[-1] if base64.b64encode(id.to_bytes(8, "big")) == b'CNZb1r3CAAA=' else us
                    uss.append(us)
                n.append(f"**is{d}** from {english_list(uss, 'or')}")
        noglobal = " (noglobal)"*highlight["noglobal"]
        line = f"{discord.utils.escape_markdown(highlight['name'])}{noglobal}: {english_list(n)}\n"
        embed.description += line  # type: ignore

    if not user["highlights"]:
        embed.set_footer(text="You don't have any!")
    elif len(user["highlights"]) == 1:
        embed.set_footer(text="Sometimes just one is all you need")
    else:
        embed.set_footer(text=f"Listed {len(user['highlights'])} triggers")

    await ctx.send(embed=embed)


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
    def __init__(self, string):
        self.string = string
        self.idx = 0

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
        if self.peek() in ("!", "-"):
            self.consume()
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

            if not regex_min(p):
                self.fail("regex should not match the empty string", "if you want to match any message, you don't need to provide a regex")
            return {"type": "regex", "regex": p, "flags": "".join(sorted(flags)), "negate": negate}
        elif self.consume_literal("guild:"):
            w = self.get_quoted_word()
            guild = discord.utils.get(bot.guilds, name=w)
            if not guild:
                try:
                    guild = bot.get_guild(int(w))
                except ValueError:
                    guild = None
            if not guild:
                self.fail("unknown guild")
            return {"type": "guild", "id": guild.id, "negate": negate}
        elif self.consume_literal("channel:") or self.consume_literal("in:"):
            w = self.get_quoted_word()
            if m := re.fullmatch("<#[0-9]+>", w):
                w = m.group(1)
            channel = discord.utils.get(guild.channels, name=w.removeprefix("#"))
            if not channel:
                try:
                    channel = bot.get_channel(int(w))
                except ValueError:
                    channel = None
            if not channel:
                self.fail("unknown channel")
            return {"type": "channel", "id": channel.id, "negate": negate}
        elif self.consume_literal("author:") or self.consume_literal("from:") or self.consume_literal("user"):
            w = self.get_quoted_word()
            if m := re.fullmatch("<@!?[0-9]+>", w):
                w = m.group(1)
            user = guild.get_member_named(w)
            if not user:
                try:
                    user = bot.get_user(int(w))
                except ValueError:
                    user = None
            if not user:
                self.fail("unknown user")
            return {"type": "author", "id": user.id, "negate": negate}
        elif self.consume_literal("noglobal"):
            return {"type": "noglobal"}
        else:
            self.fail(f"unknown start of token '{self.peek()}' ({unicodedata.name(self.peek()).title()})", "wrap literal strings in quotes and regular expressions in slashes")

def parse(text, guild):
    view = StringView(text)
    view.skip_ws()
    filters = []
    noglobal = False
    while not view.is_eof:
        rule = view.lex_rule(guild)
        if rule["type"] == "noglobal":
            noglobal = True
        else:
            filters.append(rule)
        view.skip_ws()
    return filters, noglobal

def add_highlight(ctx, name, filters=None, noglobal=False):
    if not filters:
        filters = [{"type": "literal", "text": name, "negate": False}, {"type": "guild", "id": ctx.guild.id, "negate": False}]
    highlights = get_user(ctx.author)["highlights"]
    for highlight in highlights:
        if highlight["name"] == name:
            highlight["filters"] = filters
            highlight["noglobal"] = noglobal
            break
    else:
        highlights.append({"name": name, "filters": filters, "noglobal": noglobal})
    save()


@bot.command(rest_is_raw=True, aliases=["update", "set", "edit", "put"])
async def add(ctx, name, *, text):
    """Add or update (upsert) a trigger. Syntax: `add trigger_name "string" /regex/ guild:Esolangs channel:#off-topic author:LyricLy`"""

    if not name:
        return await ctx.send("Empty strings as names aren't cool.")

    try:
        filters, noglobal = parse(text[1:], ctx.guild)
    except LexFailure as e:
        await ctx.send(f"Error while parsing input.\n```{e}```")
    else:
        add_highlight(ctx, name, filters, noglobal)
        await ctx.send("👍")

@bot.command()
async def remove(ctx, *names):
    """Remove one or more triggers by name."""

    highlights = config.get(str(ctx.author.id), {"highlights": []})["highlights"]
    to_remove = []
    for idx, highlight in enumerate(highlights):
        if highlight["name"] in names:
            to_remove.append(idx-len(to_remove))
    for idx in to_remove:
        highlights.pop(idx)
    save()
    await ctx.send("👍")

@bot.command()
async def clear(ctx):
    """Clears all of your highlight triggers. Consider using `disable` instead."""

    get_user(ctx.author)["highlights"] = []
    save()
    await ctx.send("👍")

@bot.command()
async def disable(ctx):
    """Disable all of your highlights."""
    get_user(ctx.author)["enabled"] = False
    save()
    await ctx.send("👍")

@bot.command()
async def enable(ctx):
    """Re-enable the bot after disabling it using `disable`."""
    get_user(ctx.author)["enabled"] = True
    save()
    await ctx.send("👍")

@bot.command()
async def raw(ctx, name):
    """Output a highlight trigger in the format used by the `add` command, to facilitate easier editing of triggers."""

    for highlight in config.get(str(ctx.author.id), {"highlights": []})["highlights"]:
        if highlight["name"] == name:
            break
    else:
        return await ctx.send("You don't have a trigger with that name.")

    o = [repr(highlight["name"])]
    for f in highlight["filters"]:
        t = f["type"]
        if t == "literal":
            o.append(repr(f['text']))
        elif t == "regex":
            r = render_pattern(f['regex'], f['flags']).replace('`', '`\u200b')
            o.append(f"``{r}``")
        elif t == "guild":
            o.append(f"guild:{f['id']}")
        elif t == "channel":
            o.append(f"channel:{f['id']}")
        elif t == "author":
            o.append(f"author:{f['id']}")
        elif t == "noglobal":
            o.append("noglobal")

    await ctx.send(" ".join(o))

@bot.command()
@commands.guild_only()
async def migrate(ctx):
    """Migrate your triggers from Danny's Highlight."""

    await ctx.send("Coolio. Do `@Highlight list` for me, please.")
    msg = await bot.wait_for("message", check=lambda m: m.author.id == 292212176494657536 and m.embeds and m.embeds[0].author.name == ctx.author.display_name)
    for trigger in msg.embeds[0].description.splitlines():
        add_highlight(ctx, trigger)
    await ctx.send("👍")

@bot.command()
async def block(ctx, *, what: Union[discord.TextChannel, discord.User]):
    user = get_user(ctx.author)
    blocked = user.setdefault("blocked", [])
    if what.id in blocked:
        await ctx.send("Already done.")
    else:
        blocked.append(what.id)
        save()
        await ctx.send("👍")

@bot.command()
async def unblock(ctx, *, what: Union[discord.TextChannel, discord.User]):
    user = get_user(ctx.author)
    blocked = user.setdefault("blocked", [])
    try:
        blocked.remove(what.id)
    except ValueError:
        await ctx.send("Already done.")
    else:
        save()
        await ctx.send("👍")


with open("token.txt") as f:
    token = f.read()
bot.run(token)
