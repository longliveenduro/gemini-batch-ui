"""Google Drive (optional): Anmeldung, Datei-ID aus einem Link, PDF laden oder als PDF exportieren."""
import re

from .config import CONF_DIR, CREDENTIALS_FILE, SCOPES, TOKEN_FILE


def drive_service():
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds or not creds.valid:
        try:
            if not (creds and creds.expired and creds.refresh_token):
                raise RefreshError("kein gültiger Login")
            creds.refresh(Request())
        except RefreshError:  # keine, abgelaufene oder widerrufene Freigabe (z. B. nach 7 Tagen im Testmodus)
            if not CREDENTIALS_FILE.exists():
                raise FileNotFoundError(f"credentials.json fehlt. Lege sie nach {CONF_DIR} (Anleitung oben im Skript).")
            creds = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES).run_local_server(port=0)
        CONF_DIR.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(creds.to_json())
    return build("drive", "v3", credentials=creds)


def drive_id(text):
    m = re.search(r"/d/([\w-]{20,})|[?&]id=([\w-]{20,})", text)
    return (m.group(1) or m.group(2)) if m else text.strip()


def drive_fetch(file_id):
    svc = drive_service()
    meta = svc.files().get(fileId=file_id, fields="name,mimeType", supportsAllDrives=True).execute()
    mime, name = meta["mimeType"], meta["name"]
    if mime.startswith("application/vnd.google-apps"):  # Docs, Slides usw. werden als PDF exportiert
        data = svc.files().export_media(fileId=file_id, mimeType="application/pdf").execute()
    elif mime == "application/pdf":
        data = svc.files().get_media(fileId=file_id, supportsAllDrives=True).execute()
    else:
        raise ValueError(f"„{name}“ ist kein PDF (Typ: {mime}).")
    return (name if name.lower().endswith(".pdf") else name + ".pdf"), data
