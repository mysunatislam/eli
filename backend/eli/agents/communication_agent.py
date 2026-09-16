"""Communication Agent: drafts, never sends.

- Email: an Outlook draft with real attachments when Outlook is installed (COM), otherwise a
  pre-filled Gmail compose window (Gmail cannot attach from a link, so the file path goes to the
  clipboard and the user attaches it).
- Chat apps: opens the app / web client and puts the text on the clipboard for the user to paste.
"""
from __future__ import annotations

import logging
import urllib.parse
import webbrowser
from pathlib import Path

log = logging.getLogger("eli.comms")

try:
    import pyperclip  # type: ignore
except Exception:
    pyperclip = None

CHAT_TARGETS = {
    "whatsapp": "https://web.whatsapp.com/",
    "slack": "slack://open",
    "discord": "discord://",
    "teams": "msteams:",
    "telegram": "https://web.telegram.org/",
    "messenger": "https://www.messenger.com/",
    "signal": "sgnl://",
    "gmail": "https://mail.google.com/",
    "outlook": "https://outlook.live.com/mail/",
}


class CommunicationAgent:
    def compose_email(self, to: str = "", subject: str = "", body: str = "", attachments=None) -> str:
        paths = [str(Path(a).expanduser()) for a in (attachments or []) if a and Path(a).expanduser().is_file()]
        missing = [a for a in (attachments or []) if a and not Path(a).expanduser().is_file()]
        note = f" (couldn't find: {', '.join(missing)})" if missing else ""
        outlook = self._outlook_draft(to, subject, body, paths)
        if outlook:
            return outlook + note
        params = {"view": "cm", "fs": "1"}
        if to:
            params["to"] = to
        if subject:
            params["su"] = subject
        if body:
            params["body"] = body
        webbrowser.open("https://mail.google.com/mail/?" + urllib.parse.urlencode(params))
        msg = f"Opened a Gmail draft{(' to ' + to) if to else ''}. I stopped before sending."
        if paths:
            if pyperclip:
                try:
                    pyperclip.copy(paths[0])
                except Exception:
                    pass
            msg += (f" Gmail can't attach files from a link, so I copied the path of {Path(paths[0]).name} to your clipboard: "
                    "click the paperclip and paste it into the file name box.")
        return msg + note

    def _outlook_draft(self, to: str, subject: str, body: str, paths: list[str]) -> str:
        try:
            import pythoncom  # type: ignore
            import win32com.client  # type: ignore
        except Exception:
            return ""
        try:
            pythoncom.CoInitialize()
            app = win32com.client.Dispatch("Outlook.Application")
            mail = app.CreateItem(0)
            if to:
                mail.To = to
            mail.Subject = subject or ""
            mail.Body = body or ""
            for p in paths:
                mail.Attachments.Add(p)
            mail.Display(False)
            att = f" with {len(paths)} attachment{'s' if len(paths) != 1 else ''}" if paths else ""
            return f"Opened an Outlook draft{(' to ' + to) if to else ''}{att}. Review it and press Send yourself."
        except Exception as e:
            log.info("Outlook not available: %s", e)
            return ""

    def open_chat(self, app: str, contact: str = "", text: str = "") -> str:
        key = (app or "").strip().lower()
        if key == "whatsapp" and contact and contact.replace("+", "").replace(" ", "").isdigit():
            url = f"https://wa.me/{contact.replace('+', '').replace(' ', '')}"
            if text:
                url += "?text=" + urllib.parse.quote(text)
            webbrowser.open(url)
            return f"Opened a WhatsApp chat with {contact}. The message is pre-filled; press Send yourself."
        target = CHAT_TARGETS.get(key)
        if not target:
            return f"I don't know how to open '{app}'. Try WhatsApp, Slack, Discord, Teams, Telegram or Messenger."
        try:
            webbrowser.open(target)
        except Exception as e:
            return f"Couldn't open {app}: {e}"
        extra = ""
        if text and pyperclip:
            try:
                pyperclip.copy(text)
                extra = " Your message is on the clipboard: open the chat, paste with Ctrl+V and send it yourself."
            except Exception:
                pass
        who = f" (find {contact})" if contact else ""
        return f"Opened {app}{who}.{extra}"
