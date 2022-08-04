import os
import base64
import requests as requests

import discord
from discord.ext import tasks, commands

import time
from collections import deque

# Initialize the bot
id_dir = os.path.dirname(__file__)

CHANNEL_ID = int(open(os.path.join(id_dir, "channel.txt"), "r").readline())
TOKEN = open(os.path.join(id_dir, "token.txt"), "r").readline()
VALID_VOICES = set([voice.strip() for voice in open(os.path.join(id_dir, "valid_voices.txt"), "r").readlines()])

INACTIVITY_TIMEOUT = 30
voice_text_channel = None

bot = commands.Bot(command_prefix=".", intents=discord.Intents().all())

# Queue and bot status
user_profiles = {}
message_queue = deque()
is_playing = False
last_message_time = time.time()


# Classes to store user and request data
class UserProfile:
    def __init__(self, user, is_talking=True, voice="en_us_002", say_name=True):
        self.user = user
        self.is_talking = is_talking
        self.say_name = say_name
        self.voice = voice


class TTSMessage:
    def __init__(self, user, message):
        self.user = user
        self.message = message

    def play(self):
        profile = user_profiles[self.user]

        # Create the mp3 speech file
        audio_filename = f"voice_messages/out{int(time.time())}.mp3"
        content = (f"{self.user.display_name} said " if profile.say_name else "") + self.message.content
        create_tts_mp3(content, profile.voice, audio_filename)

        # Get the voice client and play the mp3
        voice_client = discord.utils.get(bot.voice_clients, guild=self.user.guild)
        voice_client.play(discord.FFmpegPCMAudio(source=audio_filename), after=lambda x: advance_message_queue())


@bot.event
async def on_ready():
    global voice_text_channel
    voice_text_channel = bot.get_channel(CHANNEL_ID)
    activity_check.start()

    activity = discord.Game(name=".info")
    await bot.change_presence(status=discord.Status.online, activity=activity)

    print(f"Logged in as {bot.user}")


@bot.event
async def on_message(message):
    global is_playing

    if message.content.startswith(bot.command_prefix):
        await bot.process_commands(message)
        return
    if not message.channel == voice_text_channel:
        return
    user = message.author

    if user not in user_profiles:
        return
    profile = user_profiles[user]

    # Check that the user is in a voice channel and has enabled talking
    if not (user.voice and profile.is_talking):
        return

    # Check that the bot is in the correct voice channel
    voice_client = discord.utils.get(bot.voice_clients, guild=user.guild)
    if not (voice_client and voice_client.is_connected()):
        await user.voice.channel.connect()

    message_queue.append(TTSMessage(user, message))

    if not is_playing:
        is_playing = True
        advance_message_queue()


@bot.command()
async def start(ctx):
    user = ctx.message.author
    if user not in user_profiles:
        user_profiles[user] = UserProfile(user)
    user_profiles[user].is_talking = True
    await ctx.message.channel.send(f":green_circle: *{user}*, your TTS is now **ON**")


@bot.command()
async def stop(ctx):
    user = ctx.message.author
    user_profiles[user].is_talking = False
    await ctx.message.channel.send(f":red_circle: *{user}*, your TTS is now **OFF**")


@bot.command()
async def config(ctx, *args):
    user = ctx.message.author
    if user not in user_profiles:
        user_profiles[user] = UserProfile(user, is_talking=False)
    match args[0]:
        case "voice":
            if args[1] in VALID_VOICES:
                user_profiles[user].voice = args[1]
                await ctx.message.channel.send(f":pencil: *{user}*, your voice has been updated to `{args[1]}`")
            else:
                await ctx.message.channel.send(f":x: *{user}*, the voice you selected does not exist. "
                                               f"Search for available voices with `voicelist`")
        case "name":
            if args[1].lower() in ["true", "t", "yes", "y"]:
                user_profiles[user].say_name = True
            elif args[1].lower() in ["false", "f", "no", "n"]:
                user_profiles[user].say_name = False
            else:
                await ctx.message.channel.send(f":x: *{user}*, your choice could not be parsed as True or False")
                return
            await ctx.message.channel.send(f":pencil: *{user}*, "
                                           f"your name status has been updated to `{user_profiles[user].say_name}`")


@bot.command()
async def info(ctx):
    embed = discord.Embed(
        title="Help",
        description="Commands:\n " 
                    "`myprofile`: Shows your profile status with the bot\n"
                    "`info`: Prints list of commands and usage\n" 
                    "`voicelist`: Prints all voices currently supported\n"
                    "`config voice {voice}`: Updates your voice with the input you specify\n"
                    "`config name {yes/no}`: Updates if the bot will say your name before your messages\n"
                    "`start`: Allows the bot to start listening for your messages in the TTS channel\n"
                    "`stop`: Stops the bot from listening to your messages in the TTS channel\n"
    )
    await ctx.message.channel.send(embed=embed)


@bot.command()
async def voicelist(ctx):
    embed = discord.Embed(
        title="Voice List",
        description=", ".join([f"`{voice}`" for voice in VALID_VOICES])
    )
    await ctx.message.channel.send(embed=embed)


@bot.command()
async def myprofile(ctx):
    user = ctx.message.author
    if user not in user_profiles:
        user_profiles[user] = UserProfile(user, is_talking=False)
    embed = discord.Embed(
        title="Profile",
        description=(":green_circle: TTS Active" if user_profiles[user].is_talking else ":red_circle: TTS Inactive") +
                    f"\n:loud_sound: Voice: {user_profiles[user].voice}\n"
                    f":scroll: Say Name: {user_profiles[user].say_name}\n",
        color=0x5056c7
    )
    embed.set_author(name=user, icon_url=str(user.avatar_url))
    await ctx.message.channel.send(embed=embed)


@tasks.loop(seconds=2.0)
async def activity_check():
    voice = discord.utils.get(bot.voice_clients)
    if (not is_playing) and voice and voice.is_connected() and time.time() - last_message_time > INACTIVITY_TIMEOUT:
        await voice.disconnect()
        await voice_text_channel.send(f":pause_button: Left voice channel after {INACTIVITY_TIMEOUT} "
                                      f"seconds of inactivity. Talk to prompt the bot to rejoin")


def advance_message_queue():
    global is_playing, last_message_time
    if not message_queue:
        # Delete all temporary voice files
        for file in os.listdir(os.path.join(id_dir, "voice_messages")):
            os.remove(os.path.join(id_dir, "voice_messages/" + file))

        is_playing = False
        return
    voice_request = message_queue.popleft()
    last_message_time = time.time()
    voice_request.play()


def create_tts_mp3(text, voice, filename):
    # Use TikTok API to request a TTS audio file
    url = f"https://api16-normal-useast5.us.tiktokv.com/media/api/text/speech/invoke/?" \
          f"text_speaker={voice}&" \
          f"req_text={text}" \
          f"&speaker_map_type=0"

    r = requests.post(url)

    b64d = base64.b64decode([r.json()["data"]["v_str"]][0])

    out = open(filename, "wb")
    out.write(b64d)
    out.close()


bot.run(TOKEN)
