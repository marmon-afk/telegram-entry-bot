# Telegram Entry Bot

A Python bot that collects membership applications, sends them to moderators, and manages entry to a private Telegram channel.

- Collects answers to a short questionnaire in a private chat.
- Lets administrators approve or decline applications using Telegram buttons.
- Keeps an application and decision history in a local SQLite database.
- Generates invite links and processes channel join requests.
- Uses the Telegram Bot API and the Python standard library.

## Run locally

1. Create a bot with @BotFather and add it as an administrator of the target private channel with permission to manage invite links.
2. Run `python entry_bot.py`. Paste your bot token at the hidden prompt, or supply it through the `TELEGRAM_BOT_TOKEN` environment variable.
3. Use `/setup` in the bot's private chat and in the target channel. Test the application flow with a separate Telegram account.

The bot creates its own SQLite database. Keep the database and Telegram token outside version control.
