"""Tests der Google-Drive-Anbindung: Anmeldung (Token, Neuanmeldung) und Laden von Dateien. Ohne Browser, ohne Netz."""
import pytest
from google.auth.exceptions import RefreshError, TransportError

from fakes import FakeDrive, install_drive


# ---------- Anmeldung ----------
class Expired:
    valid, expired, refresh_token = False, True, "r"

    def refresh(self, request):
        raise RefreshError("invalid_grant: Token has been expired or revoked.")


class Refreshable:
    valid, expired, refresh_token = False, True, "r"

    def refresh(self, request):
        self.valid = True

    def to_json(self):
        return '{"erneuert": true}'


class Offline(Expired):
    def refresh(self, request):
        raise TransportError("keine Verbindung")


class Valid:
    valid = True

    def to_json(self):
        return '{"darf_nicht_geschrieben_werden": true}'


class NewLogin:
    valid = True

    def to_json(self):
        return '{"neue_anmeldung": true}'


@pytest.fixture
def login(monkeypatch, home):
    """Steuert die Anmeldung. login(creds_cls, token=True, credentials=True) liefert (benutzte Pfade, conf-Ordner)."""
    import google.oauth2.credentials as gc
    import google_auth_oauthlib.flow as gf
    import googleapiclient.discovery as gd

    def setup(creds_cls, *, token=True, credentials=True):
        conf = home / "conf"
        conf.mkdir(parents=True, exist_ok=True)
        if token:
            (conf / "token.json").write_text('{"alt": true}')
        if credentials:
            (conf / "credentials.json").write_text("{}")
        seen = {}

        def load_token(path, scopes):
            seen["token_path"] = path
            return creds_cls()

        class Flow:
            def run_local_server(self, port):
                seen["browser_flow"] = True
                return NewLogin()

        def load_client(path, scopes):
            seen["credentials_path"] = path
            return Flow()

        monkeypatch.setattr(gc.Credentials, "from_authorized_user_file", staticmethod(load_token))
        monkeypatch.setattr(gf.InstalledAppFlow, "from_client_secrets_file", staticmethod(load_client))
        monkeypatch.setattr(gd, "build", lambda *args, **kwargs: "DIENST")
        return seen, conf

    return setup


def test_an_expired_login_starts_a_new_browser_login(app, login):
    seen, conf = login(Expired)
    assert app.drive_service() == "DIENST"
    assert seen["browser_flow"] and seen["credentials_path"] == str(conf / "credentials.json")
    assert (conf / "token.json").read_text() == '{"neue_anmeldung": true}'


def test_an_expired_login_without_credentials_file_explains_where_to_put_it(app, login):
    _, conf = login(Expired, credentials=False)
    with pytest.raises(FileNotFoundError, match="credentials.json") as error:
        app.drive_service()
    assert str(conf) in str(error.value)


def test_a_refreshable_token_is_renewed_without_a_browser(app, login):
    seen, conf = login(Refreshable)
    assert app.drive_service() == "DIENST"
    assert "browser_flow" not in seen and (conf / "token.json").read_text() == '{"erneuert": true}'


def test_a_valid_token_is_left_alone(app, login):
    seen, conf = login(Valid)
    assert app.drive_service() == "DIENST"
    assert "browser_flow" not in seen and (conf / "token.json").read_text() == '{"alt": true}'
    assert seen["token_path"] == str(conf / "token.json")


def test_without_token_the_first_login_opens_the_browser(app, login):
    seen, conf = login(Valid, token=False)
    assert app.drive_service() == "DIENST"
    assert seen["browser_flow"] and (conf / "token.json").read_text() == '{"neue_anmeldung": true}'


def test_without_token_and_credentials_the_error_is_clear(app, login):
    login(Valid, token=False, credentials=False)
    with pytest.raises(FileNotFoundError, match="credentials.json"):
        app.drive_service()


def test_a_network_error_is_not_mistaken_for_an_expired_login(app, login):
    seen, conf = login(Offline)
    with pytest.raises(TransportError):
        app.drive_service()
    assert "browser_flow" not in seen and (conf / "token.json").read_text() == '{"alt": true}'


# ---------- Dateien laden ----------
PDF = "application/pdf"
DOC = "application/vnd.google-apps.document"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest.fixture
def drive(monkeypatch, home):
    return install_drive(monkeypatch, home, FakeDrive({
        "id-pdf": ("Buch.pdf", PDF, b"PDF-BYTES"),
        "id-ohne-endung": ("Notizen", PDF, b"PDF-OHNE-ENDUNG"),
        "id-doc": ("Protokoll", DOC, b"EXPORT-BYTES"),
        "id-docx": ("Brief.docx", DOCX, b"WORD"),
    }))


def test_drive_fetch_downloads_a_pdf(app, drive):
    assert app.drive_fetch("id-pdf") == ("Buch.pdf", b"PDF-BYTES")
    assert drive.downloaded == ["id-pdf"] and drive.exported == []


def test_drive_fetch_adds_the_pdf_suffix(app, drive):
    assert app.drive_fetch("id-ohne-endung")[0] == "Notizen.pdf"


def test_drive_fetch_exports_google_documents_as_pdf(app, drive):
    assert app.drive_fetch("id-doc") == ("Protokoll.pdf", b"EXPORT-BYTES")
    assert drive.exported == [("id-doc", "application/pdf")] and drive.downloaded == []


def test_drive_fetch_rejects_other_file_types(app, drive):
    with pytest.raises(ValueError, match="kein PDF"):
        app.drive_fetch("id-docx")
