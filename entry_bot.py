import getpass
import json
import os
import secrets
import sqlite3
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
QUESTIONS = (
    "Что значит «не мешать происходящему хаосу»? Где проходит граница между этим принципом и причинением вреда?",
    "Какое из восьми правил ты бы применил в своей жизни? Приведи конкретный пример.",
    "Что ты хочешь получить от круга своих и чем можешь быть полезен его участникам?",
)
INTRO = (
    "Представь башню из «Дженги». С каждым ходом она становится выше, но теряет устойчивость. "
    "В дженгизме башня — образ современного порядка. Пока она стоит, дженгист пользуется её "
    "возможностями, развивает навыки, укрепляет независимость и собирает круг своих. "
    "Полный текст — в документе. После ознакомления нажми «Перейти к вопросам»."
)


class TelegramError(Exception):
    pass


def api(method, **fields):
    body = json.dumps(fields).encode()
    headers = {"Content-Type": "application/json"}
    if method == "sendDocument":
        boundary = secrets.token_hex(16)
        parts = []
        for key, value in fields.items():
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
            )
        parts.extend([
            f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="Dzhengizm.docx"\r\n'
            'Content-Type: application/vnd.openxmlformats-officedocument.wordprocessingml.document\r\n\r\n'.encode(),
            document.read_bytes(),
            f"\r\n--{boundary}--\r\n".encode(),
        ])
        body = b"".join(parts)
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    request = urllib.request.Request(base + method, body, headers)
    try:
        with urllib.request.urlopen(request, timeout=40) as response:
            return json.load(response)["result"]
    except urllib.error.HTTPError as error:
        raise TelegramError(f"{method}: {json.load(error).get('description', error.code)}") from None
    except (OSError, TimeoutError):
        raise TelegramError(f"{method}: ошибка соединения") from None


def send(chat, message, buttons=None):
    fields = {"chat_id": chat, "text": message}
    if buttons:
        fields["reply_markup"] = {"inline_keyboard": [buttons]}
    api("sendMessage", **fields)


def row(user):
    return db.execute("SELECT * FROM applications WHERE user = ?", (user,)).fetchone()


def is_admin(user):
    return user == admin or db.execute(
        "SELECT 1 FROM admins WHERE user = ?", (user,)
    ).fetchone() is not None


def notify_admins(text, buttons=None):
    recipients = {admin} | {entry[0] for entry in db.execute("SELECT user FROM admins")}
    for recipient in recipients:
        try:
            send(recipient, text, buttons)
        except TelegramError as error:
            print(f"Не удалось уведомить администратора {recipient}: {error}", flush=True)


def profile_button(user, username):
    url = f"https://t.me/{username}" if username else f"tg://user?id={user}"
    return {"text": f"@{username}" if username else "Открыть профиль", "url": url}


def history(chat, offset=0):
    entries = db.execute(
        "SELECT * FROM applications WHERE status != 'draft' "
        "ORDER BY submitted_at DESC, user DESC LIMIT 6 OFFSET ?", (offset,)
    ).fetchall()
    if not entries:
        send(chat, "В истории пока нет заявок.")
        return
    labels = {"pending": "На рассмотрении", "approved": "Одобрена",
              "rejected": "Отклонена", "joined": "Одобрена, участник вступил"}
    lines, keyboard = [], []
    for index, entry in enumerate(entries[:5], offset + 1):
        submitted = entry["submitted_at"]
        stamp = (datetime.fromtimestamp(submitted, timezone(timedelta(hours=3)))
                 .strftime("%d.%m.%Y %H:%M МСК") if submitted else "время не записано")
        name = entry["display_name"] or (
            f'@{entry["username"]}' if entry["username"] else f'Telegram ID {entry["user"]}'
        )
        lines.append(f'{index}. {name}\n{stamp} · {labels[entry["status"]]}')
        button = profile_button(entry["user"], entry["username"])
        button["text"] = f'{index}. {button["text"]}'
        keyboard.append([button])
    navigation = []
    if offset:
        navigation.append({"text": "Назад", "callback_data": f"history:{offset - 5}"})
    if len(entries) > 5:
        navigation.append({"text": "Дальше", "callback_data": f"history:{offset + 5}"})
    if navigation:
        keyboard.append(navigation)
    api("sendMessage", chat_id=chat, text="История заявок\n\n" + "\n\n".join(lines),
        reply_markup={"inline_keyboard": keyboard})


def invite(user):
    prior = row(user)["link"]
    if prior and row(user)["expires"] > time.time():
        api("revokeChatInviteLink", chat_id=channel, invite_link=prior)
    expires = int(time.time()) + 86400
    link = api("createChatInviteLink", chat_id=channel, expire_date=expires, creates_join_request=True)["invite_link"]
    db.execute("UPDATE applications SET link = ?, expires = ? WHERE user = ?", (link, expires, user))
    db.commit()
    send(user, "Анкета одобрена. Подай заявку по персональной ссылке. Она действует 24 часа.", [
        {"text": "Подать заявку", "url": link}
    ])


def message(event):
    if event["chat"]["type"] != "private":
        return
    user = event["from"]["id"]
    value = event.get("text", "").strip()
    if value == "/id":
        send(user, f"Твой Telegram ID: {user}")
        return
    command, _, argument = value.partition(" ")
    if command in ("/addadmin", "/removeadmin"):
        if user != admin:
            send(user, "Управлять администраторами может только владелец бота.")
            return
        argument = argument.strip()
        if not argument.isascii() or not argument.isdecimal() or not 0 < int(argument) < 2**63:
            send(user, f"Используй: {command} Telegram_ID. Новый администратор может узнать ID командой /id.")
            return
        target = int(argument)
        if target == admin:
            send(user, "Владелец уже имеет постоянный доступ.")
            return
        if command == "/addadmin":
            try:
                send(target, "Ты добавлен администратором бота. Новые заявки будут приходить сюда. История: /history.")
            except TelegramError:
                send(user, "Не удалось написать этому пользователю. Пусть сначала отправит боту /id; затем проверь ID и повтори команду.")
                return
            db.execute("INSERT OR IGNORE INTO admins (user) VALUES (?)", (target,))
        else:
            db.execute("DELETE FROM admins WHERE user = ?", (target,))
        db.commit()
        send(user, "Администратор добавлен." if command == "/addadmin" else "Доступ администратора удалён.")
        return
    if is_admin(user) and value == "/history":
        history(user)
        return
    current = row(user)
    status = current["status"] if current else None
    if value == "/start":
        if status == "approved":
            invite(user)
        elif status in ("pending", "rejected", "joined"):
            send(user, {"pending": "Анкета на рассмотрении.", "rejected": "Анкета отклонена.",
                        "joined": "Твоя заявка уже одобрена."}[status])
        elif status == "draft":
            send(user, QUESTIONS[len(json.loads(current["answers"]))])
        else:
            api("sendDocument", chat_id=user)
            send(user, INTRO, [{"text": "Перейти к вопросам", "callback_data": "begin"}])
        return
    if status != "draft":
        return
    if not value or len(value) > 800 or value.startswith("/"):
        send(user, "Напиши ответ текстом, до 800 символов.")
        return
    answers = json.loads(current["answers"])
    answers.append(value)
    if len(answers) == len(QUESTIONS):
        sender = event["from"]
        username = sender.get("username")
        name = " ".join(filter(None, (sender.get("first_name"), sender.get("last_name"))))
        review = "\n\n".join(f"{i}. {q}\n{a}" for i, (q, a) in enumerate(zip(QUESTIONS, answers), 1))
        db.execute(
            "UPDATE applications SET answers = ?, status = 'pending', username = ?, "
            "display_name = ?, submitted_at = ? WHERE user = ?",
            (json.dumps(answers), username, name, int(time.time()), user),
        )
        db.commit()
        notify_admins(f"Анкета от {name}\n\n{review}", [
            profile_button(user, username),
            {"text": "Принять", "callback_data": f"approve:{user}"},
            {"text": "Отклонить", "callback_data": f"reject:{user}"},
        ])
        send(user, "Анкета отправлена на рассмотрение.")
    else:
        db.execute("UPDATE applications SET answers = ? WHERE user = ?", (json.dumps(answers), user))
        db.commit()
        send(user, QUESTIONS[len(answers)])


def callback(event):
    user = event["from"]["id"]
    action, _, target = event.get("data", "").partition(":")
    try:
        api("answerCallbackQuery", callback_query_id=event["id"])
    except TelegramError as error:
        if "query is too old" in str(error):
            return
        raise
    if is_admin(user) and action == "history" and target.isdecimal():
        history(user, int(target))
        return
    if action == "begin":
        db.execute("INSERT OR IGNORE INTO applications (user) VALUES (?)", (user,))
        db.commit()
        current = row(user)
        if current["status"] == "draft":
            send(user, QUESTIONS[len(json.loads(current["answers"]))])
    if not is_admin(user) or action not in ("approve", "reject") or not target.isdecimal():
        return
    target = int(target)
    current = row(target)
    if not current or current["status"] != "pending":
        send(user, "Эта анкета уже обработана.")
        return
    status = "approved" if action == "approve" else "rejected"
    db.execute("UPDATE applications SET status = ? WHERE user = ?", (status, target))
    db.commit()
    notify_admins(f"Заявка пользователя {target}: {'одобрена' if action == 'approve' else 'отклонена'} администратором {user}.")
    if action == "approve":
        invite(target)
    else:
        send(target, "Анкета отклонена.")


def join(event):
    if event["chat"]["id"] != channel:
        return
    user = event["from"]["id"]
    current = row(user)
    requested_link = event.get("invite_link", {}).get("invite_link")
    if not (current and current["status"] == "approved" and requested_link
            and requested_link == current["link"] and current["expires"] > time.time()):
        api("declineChatJoinRequest", chat_id=channel, user_id=user)
        return
    api("approveChatJoinRequest", chat_id=channel, user_id=user)
    db.execute("UPDATE applications SET status = 'joined' WHERE user = ?", (user,))
    db.commit()
    api("revokeChatInviteLink", chat_id=channel, invite_link=requested_link)


if __name__ == "__main__":
    document = ROOT / "Dzhengizm.docx"
    if not document.is_file():
        raise SystemExit("Положи Dzhengizm.docx рядом с entry_bot.py.")
    token = os.environ.pop("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        token = getpass.getpass("Токен BotFather: ").strip()
    base = "https://api.telegram.org/bot" + token + "/"
    me = api("getMe")
    if me["username"].lower() != "dzhengizmentrybot":
        raise SystemExit("Токен принадлежит другому боту.")
    db = sqlite3.connect(ROOT / "entry_bot.sqlite3")
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value INTEGER);
        CREATE TABLE IF NOT EXISTS admins (user INTEGER PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS applications (
            user INTEGER PRIMARY KEY, status TEXT DEFAULT 'draft', answers TEXT DEFAULT '[]',
            link TEXT, expires INTEGER
        );
    """)
    settings = dict(db.execute("SELECT name, value FROM settings"))
    columns = {column[1] for column in db.execute("PRAGMA table_info(applications)")}
    for name, kind in (("username", "TEXT"), ("display_name", "TEXT"), ("submitted_at", "INTEGER")):
        if name not in columns:
            db.execute(f"ALTER TABLE applications ADD COLUMN {name} {kind}")
    db.commit()
    admin, channel = settings.get("admin"), settings.get("channel")
    if not admin or not channel:
        print("Сейчас отправь боту /setup в личке и опубликуй новый пост /setup в закрытом канале.")
        print("Терминал покажет два ID. Сверь события и введи эти ID после остановки Ctrl+C.")
    else:
        print(f"@{me['username']} запущен. Для проверки отправь /history в личку боту.", flush=True)
    offset = None
    try:
        while True:
            try:
                updates = api("getUpdates", offset=offset, timeout=25,
                              allowed_updates=["message", "channel_post", "callback_query", "chat_join_request"])
            except TelegramError as error:
                print(error)
                time.sleep(3)
                continue
            for update in updates:
                offset = update["update_id"] + 1
                try:
                    if not admin or not channel:
                        for key in ("message", "channel_post"):
                            event = update.get(key, {})
                            if event.get("text") == "/setup":
                                print(f'{event["chat"]["type"]} ID = {event["chat"]["id"]}')
                    elif "message" in update:
                        message(update["message"])
                    elif "callback_query" in update:
                        callback(update["callback_query"])
                    elif "chat_join_request" in update:
                        join(update["chat_join_request"])
                except TelegramError as error:
                    print(error)
    except KeyboardInterrupt:
        if not admin or not channel:
            admin = int(input("\nТвой private ID: "))
            channel = int(input("channel ID: "))
            chat = api("getChat", chat_id=channel)
            membership = api("getChatMember", chat_id=channel, user_id=me["id"])
            if chat["type"] != "channel" or chat.get("username") or not membership.get("can_invite_users"):
                raise SystemExit("Нужен закрытый канал и право бота приглашать пользователей.")
            db.executemany("INSERT OR REPLACE INTO settings VALUES (?, ?)", [("admin", admin), ("channel", channel)])
            db.commit()
            print("Настройки записаны. Запусти скрипт снова.")
