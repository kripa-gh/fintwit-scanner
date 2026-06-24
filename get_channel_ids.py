# get_channel_ids.py
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

API_ID = 33966559
API_HASH = "48cb2c4019cfaff73bf5adb60da9b8bf"
SESSION = "1BJWap1sBu8Th7tL14aa6Kwi0YEmItJdA8XJ_tVHnLwlDwzMXqrPRqYmYAValoOgLq_CfeYzR446jsiQNzACFJ-TGDXoAvbOhIz2rhk48MxW5dap-x38hZCehso6PjLtZFM5wY9RsY3JaHdyOZU2YFgMFRefZVFSbRL3h7WowwpEPqj-6C4v6WICS83tL8nsNoCo9l73V49UlvxbAGyJTdQk-iTBcJI6BeEoaVmIKGaGXWFbOjHY8eH6WPIVcEtajBeABarIs7DG1w33BVS_EcSjYLcMMNRj0noGLGtqbsCgQUljvsOhgF8Y9IAJ477Ubc8e-alYiIpJmoL6ViQc9K7A30hzIwxw="

with TelegramClient(StringSession(SESSION), API_ID, API_HASH) as client:
    for dialog in client.iter_dialogs():
        print(f"{dialog.id} | {dialog.name}")