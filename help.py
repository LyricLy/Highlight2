import discord
from discord.ext.commands import HelpCommand


class HighlightHelpCommand(HelpCommand):
    def __init__(self):
        super().__init__()

    def get_command_signature(self, command):
        return f"{command.name} {command.signature}"

    async def send_bot_help(self, mapping):
        embed = discord.Embed()
        for command in await self.filter_commands(self.context.bot.commands, sort=True):
            embed.add_field(name=self.get_command_signature(command), value=command.short_doc, inline=False)
        embed.set_footer(text="Use `help command` for more info on a command.")
        await self.get_destination().send(embed=embed)

    async def send_group_help(self, group):
        embed = discord.Embed(title=self.get_command_signature(group), description=group.help)
        for command in await self.filter_commands(group.commands, sort=True):
            embed.add_field(name=self.get_command_signature(command),
                                                                      value=command.short_doc,  # type: ignore
                                                                                               inline=False)
        embed.set_footer(text=f"Use `help {group.qualified_name} command` for more info on a subcommand.")
        await self.get_destination().send(embed=embed)

    async def send_command_help(self, command):
        embed = discord.Embed(title=self.get_command_signature(command), description=command.help)
        await self.get_destination().send(embed=embed)
