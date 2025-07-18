import asyncio
import base64
import datetime

import time
import json
import unicodedata
import os
from collections import defaultdict
from typing import Union, Optional

import re2 as re
import discord
from discord.utils import escape_markdown as escape
from discord.ext import commands

import hlparser as parser
from utils import render_pattern, matches, english_list, sanitize_markdown
from help import HighlightHelpCommand


intents = discord.Intents(
    guilds=True,
    messages=True,
    typing=True,
    reactions=True,
    members=True,
    message_content=True,
)

bot = commands.Bot(
    command_prefix=commands.when_mentioned,
    description="A highlighting bot that DMs you when someone says something that matches a preconfigured set of criteria.",
    max_messages=None,
    allowed_mentions=discord.AllowedMentions(everyone=False),
    intents=intents,
    help_command=HighlightHelpCommand(),
)
async def setup():
    await bot.load_extension("jishaku")
bot.setup_hook = setup
last_active = defaultdict(float)
last_highlight = defaultdict(float)

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
    "debounce_time": ("debounce-cooldown", "Debounce cooldown", "The cooldown between highlights so you don't get highlighted multiple times in a row.", 10, int),
    "debounce_global": ("debounce-global", "Global debouncing", "Whether to apply the debounce cooldown across all rules instead of per rule.", False, bool),
    "debounce_fixed": ("debounce-fixed", "Fixed debounce window", "Apply a fixed window for debouncing. "
                                                                  "Without this option enabled, the debounce cooldown will reset every time a highlight triggers, "
                                                                  "even if the cooldown has not run out yet.", True, bool),
    "mention_activity": ("mention-activity", "Mention activity", "Treat people mentioning (pinging) you as activity for the purposes of cooldowns.", False, bool),
}

@bot.group(invoke_without_command=True, name="settings", aliases=["opt", "cfg", "config"])
async def _settings(ctx):
    """Display or set configuration options."""

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
        try:
            o = re.compile(regex, options)
        except re.error:
            return False
        regex_cache[key] = o

    return o.search(content)

def english_list(l, merger="and"):
    if len(l) == 1:
        return f"{l[0]}"
    elif len(l) == 2:
        return f"{l[0]} {merger} {l[1]}"
    else:
        return f"{', '.join(l[:-1])}, {merger} {l[-1]}"

async def send_highlight(user, patterns, msg, provenance):
    before = [x async for x in msg.channel.history(before=msg, limit=2)][::-1]
    after = [x async for x in msg.channel.history(after=msg, limit=2)]

    lines = []
    for message in before + [None] + after:
        bold = not message
        if bold:
            message = msg

        timestamp = discord.utils.format_dt(message.created_at, "t")  # type: ignore
        head_str = f"[{timestamp}] {escape(message.author.display_name)}"
        if bold:
            head_str = f"**{head_str}**"

        content = message.content
        if len(content) > 700:
            content = f"{content[:700]}..."

        lines.append(f"{head_str}: {sanitize_markdown(content)}")

    embed = discord.Embed(description='\n'.join(lines))
    embed.add_field(name="\u200b", value=msg.jump_url)

    pattern_string = english_list([repr(x) for x in patterns])
    highlights = "Highlight" if len(patterns) == 1 else "Highlights"
    try:
        await user.send(f'{highlights} {pattern_string} in {msg.channel.mention} (on **{msg.guild.name}**) by {provenance.mention} ({provenance.display_name})', embed=embed)
    except discord.HTTPException:
        pass

def merge_filters(filters):
    rules = defaultdict(list)
    out_filters = []
    for f in filters:
        if f["type"] in ("guild", "channel", "exact_channel", "author"):
            if not f["negate"]:
                rules[f["type"]].append(f['id'])
            else:
                out_filters.append({"type": f["type"], "ids": [f["id"]], "negate": True})
        else:
            out_filters.append(f)
    for k, v in rules.items():
        out_filters.append({"type": k, "ids": list(set(v)), "negate": False})
    return out_filters

def check_single_debounce(user, key):
    if time.time()-last_highlight[key] <= get_config(user, "debounce_time"):
        if not get_config(user, "debounce_fixed"):
            last_highlight[key] = time.time()
        return False
    last_highlight[key] = time.time()
    return True

def do_debounce(channel_id, user_id, user, successes):
    if get_config(user, "debounce_global") and successes:
        return successes*check_single_debounce(user, (channel_id, user_id))
    else:
        return [success for success in successes if check_single_debounce(user, (channel_id, user_id, success))]

def regex_of_fixed(string):
    r = re.escape(string)
    if re.search(r"^\w", string):
        r = r"\b" + r
    if re.search(r"\w$", string):
        r = r + r"\b"
    return r

def successes_of_message(user, message, relevant_react=None):
    successes = []
    global_result = True

    for highlight in user["highlights"]:
        if highlight["name"] == "global" and any(f["type"] == "react" for f in highlight["filters"]):
            # if something in the global rule is relevant, every rule is relevant
            relevant_react = None
            break

    for highlight in user["highlights"]:
        is_global = highlight["name"] == "global"
        is_relevant = not relevant_react
        for f in merge_filters(highlight["filters"]):
            t = f["type"]
            if t == "literal":
                x = matches(regex_of_fixed(f['text']), message.content, "i")
            elif t == "regex":
                x = matches(f['regex'], message.content, f['flags'])
            elif t == "react":
                is_relevant = is_relevant or f['emoji'] == relevant_react
                x = any(str(r.emoji) == f['emoji'] for r in message.reactions)
            elif t == "guild":
                x = message.guild.id in f['ids']
            elif t in ("channel", "exact_channel"):
                x = message.channel.id in f['ids'] or t == "channel" and getattr(message.channel, "parent_id", None) in f['ids']
            elif t == "author":
                x = message.author.id in f['ids']
            elif t == "bot":
                x = message.author.bot
            else:
                assert False
            if bool(x) != (not f["negate"]):
                break
        else:
            if is_relevant and not is_global:
                successes.append(highlight)
            continue
        if is_global:
            global_result = False

    return [x["name"] for x in successes if global_result or x["noglobal"]]

async def check_highlights(message, provenance, relevant_react=None):
    if not message.guild:
        return

    activity_matters = datetime.datetime.now(datetime.timezone.utc) - message.created_at < datetime.timedelta(minutes=5)

    users_to_highlight = defaultdict(list)
    for id, user in config.items():
        user_obj = message.guild.get_member(int(id))
        if not user_obj:
            continue

        if get_config(user, "mention_activity") and user_obj.mentioned_in(message):
            last_active[(message.channel.id, int(id))] = time.time()

        if (not message.channel.permissions_for(user_obj).read_messages or not user.get("enabled", True)
         or message.author.id in (blocked := user.get("blocked", [])) or message.channel.id in blocked or getattr(message.channel, "parent_id", None) in blocked):
            continue

        start_last_active = last_active.get((message.channel.id, int(id)), 0)
        activity_failure = (activity_matters or message.author == user_obj) and (
            time.time()-start_last_active <= get_config(user, "before_time")
         or user_obj.voice and user_obj.voice.channel and user_obj.voice.channel.category == message.channel.category
        )

        successes = successes_of_message(user, message, relevant_react)
        successes = do_debounce(message.channel.id, int(id), user, successes)

        if successes and not activity_failure:
            if activity_matters:
                await asyncio.sleep(get_config(user, "after_time"))
                if last_active.get((message.channel.id, int(id)), 0) > start_last_active:
                    # they spoke during the sleep
                    continue

            await send_highlight(user_obj, successes, message, provenance)

@bot.listen()
async def on_message(message):
    last_active[(message.channel.id, message.author.id)] = time.time()

    if not message.guild:
        return

    await check_highlights(message, message.author)

@bot.event
async def on_raw_reaction_add(payload):
    last_active[(payload.channel_id, payload.user_id)] = time.time()

    if not payload.guild_id:
        return

    msg = await bot.get_channel(payload.channel_id).fetch_message(payload.message_id)
    if any(str(r.emoji) == str(payload.emoji) and r.count == 1 for r in msg.reactions):
        await check_highlights(msg, payload.member, str(payload.emoji))

@bot.event
async def on_raw_reaction_remove(payload):
    last_active[(payload.channel_id, payload.user_id)] = time.time()

@bot.event
async def on_raw_message_edit(payload):
    last_active[(payload.channel_id, payload.data["author"]["id"])] = time.time()

@bot.event
async def on_typing(channel, user, when):
    last_active[(channel.id, user.id)] = when.timestamp()

@bot.command(aliases=["list"])
async def show(ctx):
    """List all of your highlight triggers."""

    user = get_user(ctx.author)

    embed = discord.Embed(title="Your highlight triggers" + " are disabled"*(not user.get("enabled", True)), description="")
    embed.set_author(name=ctx.author.display_name, icon_url=ctx.author.avatar)

    for highlight in user["highlights"]:
        n = []
        for f in merge_filters(highlight["filters"]):
            t = f["type"]
            d = " not" * f['negate']
            if t == "literal":
                n.append(f"**does{d}** contain {escape(repr(f['text']))}")
            elif t == "regex":
                n.append(f"**does{d}** match {escape(render_pattern(f['regex'], f['flags']))}")
            elif t == "react":
                n.append(f"**does{d}** have a {f['emoji']} reaction")
            elif t == "guild":
                gss = []
                for id in f['ids']:
                    g = bot.get_guild(id)
                    gs = f"server {escape(g.name)}" if g else f"<unknown server {id}>"
                    gss.append(gs)
                n.append(f"**is{d}** in {english_list(gss, 'or')}")
            elif t in ("channel", "exact_channel"):
                cs = [f"<#{id}>" for id in f['ids']]
                n.append(f"**is{d}** in {english_list(cs, 'or')}{' (excluding threads)'*(t == 'exact_channel')}")
            elif t == "author":
                uss = []
                for id in f['ids']:
                    u = bot.get_user(id)
                    us = f"<@{u.id}> ({u})" if u else f"<@{id}>"
                    uss.append(us)
                n.append(f"**is{d}** from {english_list(uss, 'or')}")
            elif t == "bot":
                n.append("**is{d}** from a bot")
        noglobal = " (noglobal)"*highlight["noglobal"]
        line = f"{escape(highlight['name'])}{noglobal}: {english_list(n)}\n"
        embed.description += line  # type: ignore

    if not user["highlights"]:
        embed.set_footer(text="You don't have any!")
    elif len(user["highlights"]) == 1:
        embed.set_footer(text="Sometimes just one is all you need")
    else:
        embed.set_footer(text=f"Listed {len(user['highlights'])} triggers")

    await ctx.send(embed=embed)


def add_highlight(ctx, name, filters=None, noglobal=False):
    if not filters:
        filters = [{"type": "literal", "text": name, "negate": False}]
        if ctx.guild:
            filters.append({"type": "guild", "id": ctx.guild.id, "negate": False})
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
        filters, noglobal = parser.parse(text[1:], ctx)
    except parser.LexFailure as e:
        return await ctx.send(f"Error while parsing input.\n```{e}```")

    try:
        parser.parse(name, ctx)
    except parser.LexFailure:
        pass
    else:
        # it's suspicious if the name of the trigger parses as a valid rule. this is probably a mistake, so we reject it.
        err = f'Refusing to create trigger with confusing name `{name}`.\nI think you meant to write `{ctx.invoked_with} "{name.strip("/+'")}" {name}`.'
        return await ctx.send(err)

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
async def test(ctx, where: Optional[discord.Message]):
    """Simulate what would happen if someone sent a certain message, ignoring all delays and debouncing."""
    if where is None:
        where = ctx.message.reference.resolved if ctx.message.reference and ctx.message.reference.resolved else ctx.message

    successes = successes_of_message(get_user(ctx.author), where)
    if not successes:
        return await ctx.send("No highlight matched.")
    await send_highlight(ctx, successes, where, ctx.author)

@bot.command()
async def raw(ctx, name):
    """Output a highlight trigger in the format used by the `add` command, to facilitate easier editing of triggers."""

    for highlight in config.get(str(ctx.author.id), {"highlights": []})["highlights"]:
        if highlight["name"] == name:
            break
    else:
        return await ctx.send("You don't have a trigger with that name.")

    if any(c.isspace() or c == '"' for c in name):
        name = name.replace('"', r'\"')
        name = f'"{name}"'

    o = [bot.user.mention, "edit", name]
    for f in highlight["filters"]:
        t = f["type"]
        if t == "literal":
            rep = repr(f['text'])
        elif t == "regex":
            r = render_pattern(f['regex'], f['flags']).replace('`', '`\u200b')
            rep = f"``{r}``"
        elif t == "react":
            rep = f"+{f['emoji']}"
        elif t in ("guild", "channel", "exact_channel", "author"):
            rep = f"{t}:{f['id']}"
        elif t == "noglobal":
            rep = "noglobal"
        elif t == "bot":
            rep = "bot"
        o.append("-"*f['negate'] + rep)

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
async def block(ctx, *, what: Union[discord.TextChannel, discord.User, discord.Thread, discord.ForumChannel]):
    """Block a user or channel from activating highlights."""

    user = get_user(ctx.author)
    blocked = user.setdefault("blocked", [])
    if what.id in blocked:
        await ctx.send("Already done.")
    else:
        blocked.append(what.id)
        save()
        await ctx.send("👍")

@bot.command()
async def unblock(ctx, *, what: Union[discord.TextChannel, discord.User, discord.Thread]):
    """Unblock a user or channel."""

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
