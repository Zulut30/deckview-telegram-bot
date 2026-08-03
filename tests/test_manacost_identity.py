import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deckview.bot import application as main
from deckview.integrations import manacost_identity
from deckview.repositories import web as web_db
from image_creator.cards_placer import (
    IMAGE_STYLE_CLASSIC,
    IMAGE_STYLE_CUSTOM,
    IMAGE_STYLE_PARCHMENT,
    _build_rust_payload,
    _make_x2_badge,
)


class _Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _Session:
    def __init__(self, *, post=None, get=None):
        self.post_response = post
        self.get_response = get
        self.last_url = None
        self.last_kwargs = None

    def post(self, url, **kwargs):
        self.last_url = url
        self.last_kwargs = kwargs
        return self.post_response

    def get(self, url, **kwargs):
        self.last_url = url
        self.last_kwargs = kwargs
        return self.get_response


class ManacostIdentityClientTests(unittest.TestCase):
    def test_device_flow_accepts_official_https_link(self):
        session = _Session(
            post=_Response(
                200,
                {
                    "device_code": "opaque-secret",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://arena.hs-manacost.ru/connect/",
                    "verification_uri_complete": (
                        "https://arena.hs-manacost.ru/connect/?user_code=ABCD-EFGH"
                    ),
                    "expires_in": 600,
                    "interval": 5,
                },
            )
        )
        with patch.object(manacost_identity, "_http_session", return_value=session):
            result = manacost_identity.start_device_authorization()
        self.assertEqual(result["user_code"], "ABCD-EFGH")
        self.assertEqual(session.last_kwargs["data"]["client_id"], "manacost-tracker")
        self.assertNotIn("X-API-Key", session.last_kwargs["headers"])

    def test_device_flow_rejects_hostile_link(self):
        session = _Session(
            post=_Response(
                200,
                {
                    "device_code": "opaque-secret",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://evil.example/connect/",
                    "verification_uri_complete": "https://evil.example/connect/code",
                    "expires_in": 600,
                    "interval": 5,
                },
            )
        )
        with patch.object(manacost_identity, "_http_session", return_value=session):
            with self.assertRaisesRegex(
                manacost_identity.ManacostIdentityError,
                "небезопасную",
            ):
                manacost_identity.start_device_authorization()

    def test_pending_token_has_typed_error(self):
        session = _Session(
            post=_Response(400, {"error": "authorization_pending"})
        )
        with patch.object(manacost_identity, "_http_session", return_value=session):
            with self.assertRaises(manacost_identity.AuthorizationPending):
                manacost_identity.exchange_device_code("opaque-secret")

    def test_profile_keeps_only_required_public_data(self):
        session = _Session(
            get=_Response(
                200,
                {
                    "user": {
                        "id": "internal-1",
                        "publicProfileId": "MC-42",
                        "profileUrl": "https://arena.hs-manacost.ru/profile/MC-42",
                        "email": "private@example.com",
                        "name": "Игрок",
                        "avatarInitials": "И",
                    },
                    "subscription": {
                        "hasAccess": True,
                        "source": "boosty",
                        "checkedAt": "2026-07-30T20:00:00Z",
                        "stale": False,
                        "entitlements": {"statistics.read": True},
                    },
                },
            )
        )
        with patch.object(manacost_identity, "_http_session", return_value=session):
            profile = manacost_identity.get_authorized_profile("access-token")
        self.assertEqual(profile["public_profile_id"], "MC-42")
        self.assertTrue(profile["has_access"])
        self.assertNotIn("email", profile)
        self.assertNotIn("access_token", profile)


class ManacostIdentityStorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(
            web_db,
            "WEB_DATABASE_PATH",
            str(Path(self.directory.name) / "identity.db"),
        )
        self.db_patch.start()
        web_db._db_initialized = False
        web_db.init_db()

    def tearDown(self):
        web_db._db_initialized = False
        self.db_patch.stop()
        self.directory.cleanup()

    @staticmethod
    def _profile(user_id="manacost-1"):
        return {
            "manacost_user_id": user_id,
            "public_profile_id": "MC-42",
            "profile_url": "https://arena.hs-manacost.ru/profile/MC-42",
            "display_name": "Игрок",
            "has_access": True,
            "subscription_source": "boosty",
            "subscription_checked_at": "2026-07-30T20:00:00Z",
            "subscription_stale": False,
            "entitlements": {"statistics.read": True},
        }

    def test_link_read_and_unlink(self):
        saved = web_db.save_manacost_identity(100, self._profile())
        self.assertEqual(saved["public_profile_id"], "MC-42")
        self.assertTrue(saved["has_access"])
        self.assertTrue(web_db.remove_manacost_identity(100))
        self.assertIsNone(web_db.get_manacost_identity(100))

    def test_identity_cannot_be_linked_to_two_telegram_users(self):
        web_db.save_manacost_identity(100, self._profile())
        with self.assertRaisesRegex(ValueError, "already linked"):
            web_db.save_manacost_identity(200, self._profile())


class ProfileAndRenderingTests(unittest.TestCase):
    def test_empty_profile_still_offers_manacost_login(self):
        text, rows = main._build_profile_display([], None)
        self.assertIn("Manacost ID", text)
        callbacks = [
            button.callback_data
            for row in rows
            for button in row
            if button.callback_data
        ]
        self.assertIn("profile_manacost_login", callbacks)

    def test_classic_and_custom_use_white_x2_badge(self):
        classic = _make_x2_badge(375, IMAGE_STYLE_CLASSIC)
        custom = _make_x2_badge(375, IMAGE_STYLE_CUSTOM)
        parchment = _make_x2_badge(375, IMAGE_STYLE_PARCHMENT)
        self.assertEqual(classic.tobytes(), custom.tobytes())
        self.assertNotEqual(classic.tobytes(), parchment.tobytes())
        self.assertEqual(classic.mode, "RGBA")
        self.assertIsNotNone(classic.getchannel("A").getbbox())
        visible = [
            pixel
            for pixel in classic.get_flattened_data()
            if pixel[3] >= 200
        ]
        bright = [
            pixel
            for pixel in visible
            if min(pixel[:3]) >= 225
        ]
        self.assertGreater(len(bright), len(visible) * 0.45)

    def test_rust_classic_renderer_uses_white_x2_asset(self):
        payload = _build_rust_payload(
            {"card-one": 2},
            {"card-one": 1},
            0,
            {"cards": [], "class": {"slug": "neutral"}},
        )
        self.assertEqual(payload["schema_version"], 1)
        water_path = payload["assets"]["water_path"]
        self.assertTrue(water_path.endswith("assets/x2-white.png"))
        self.assertTrue(Path(water_path).is_file())


if __name__ == "__main__":
    unittest.main()
