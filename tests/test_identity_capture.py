"""What Condor stores about who a person is, and when it learns it (FEAT-088).

The admin panel could not name anyone because there was nothing stored to name
them with: `users` records carried an id, a role and *sometimes* a handle, and
`first_name`/`last_name` were never persisted at all — even though `main.py`
already read the first name to mint a web login token.

Two mechanisms close that, and both are pinned here: capture on every authorized
contact (a handle changes silently, so capturing once at registration is
capturing a value that goes stale and stays stale), and the refusal to ever let
a capture make the record *worse* — an update that omits a field must not erase
what we already know.
"""

import time

import pytest

import config_manager as cm_module
from config_manager import user_display_name


@pytest.fixture
def cm(tmp_path, monkeypatch):
    """A real ConfigManager on a throwaway config.yml — persistence included."""
    monkeypatch.chdir(tmp_path)  # audit_log.yml is written relative to cwd
    manager = cm_module.ConfigManager(config_path=str(tmp_path / "config.yml"))
    manager._data["users"] = {}
    manager._audit_log = []
    manager._save_config()
    return manager


# ── the display-name ladder ──


def test_a_full_name_wins_over_the_handle():
    record = {
        "user_id": 7,
        "username": "cardosofede",
        "first_name": "Federico",
        "last_name": "Cardoso",
    }
    assert user_display_name(record) == "Federico Cardoso"


def test_a_first_name_alone_is_a_name():
    """Telegram guarantees a first name; last name and handle are both optional."""
    assert user_display_name({"user_id": 7, "first_name": "Federico"}) == "Federico"


def test_the_handle_is_the_fallback_when_no_name_was_captured():
    assert user_display_name({"user_id": 7, "username": "fengtality"}) == "fengtality"


def test_the_id_is_the_last_resort_and_reads_as_a_person():
    assert user_display_name({"user_id": 6483117755}) == "User 6483117755"


def test_an_id_with_no_record_at_all_is_still_nameable():
    """The orphan `shared_with` grant has no users entry to take a name from."""
    assert user_display_name(None, 6483117755) == "User 6483117755"


# ── registration ──


def test_registration_stores_the_names_telegram_supplied(cm):
    cm.register_pending(7, "cardosofede", first_name="Federico", last_name="Cardoso")

    record = cm.get_user(7)
    assert record["first_name"] == "Federico"
    assert record["last_name"] == "Cardoso"
    assert record["username"] == "cardosofede"
    assert user_display_name(record) == "Federico Cardoso"


def test_an_absent_handle_is_an_absent_key_not_a_placeholder(cm):
    """An omitted Telegram handle stays absent from the stored record."""
    cm.register_pending(7, None, first_name="Federico")

    assert "username" not in cm.get_user(7)


def test_registering_someone_who_already_exists_changes_nothing(cm):
    cm.register_pending(7, "original")
    assert cm.register_pending(7, "impostor") is False
    assert cm.get_user(7)["username"] == "original"


# ── capture on contact ──


def test_a_changed_handle_is_captured_on_the_next_interaction(cm):
    cm.register_pending(7, "old_handle")

    assert cm.touch_user_identity(7, username="new_handle") is True
    assert cm.get_user(7)["username"] == "new_handle"


def test_capturing_a_handle_does_not_erase_a_stored_first_name(cm):
    """The acceptance criterion: an update carries whichever fields it carries."""
    cm.register_pending(7, "old_handle", first_name="Federico", last_name="Cardoso")

    cm.touch_user_identity(7, username="new_handle")

    record = cm.get_user(7)
    assert record["first_name"] == "Federico"
    assert record["last_name"] == "Cardoso"
    assert record["username"] == "new_handle"


def test_an_update_with_no_username_does_not_erase_the_stored_one(cm):
    """Telegram omits the handle constantly; treating that as a deletion would
    make the record worse the more often we looked at it."""
    cm.register_pending(7, "cardosofede", first_name="Federico")

    cm.touch_user_identity(7, username=None, first_name="Federico")

    assert cm.get_user(7)["username"] == "cardosofede"


def test_a_blank_string_is_treated_as_absent_not_as_an_erasure(cm):
    cm.register_pending(7, "cardosofede")

    cm.touch_user_identity(7, username="   ", first_name="")

    assert cm.get_user(7)["username"] == "cardosofede"


def test_capturing_an_unknown_user_records_nothing(cm):
    """`touch` is not a back door around `register_pending`'s audited path."""
    assert cm.touch_user_identity(999, username="ghost") is False
    assert cm.get_user(999) is None


def test_the_identity_lands_in_config_yml(cm):
    cm.register_pending(7, "cardosofede")
    cm.touch_user_identity(7, first_name="Federico", last_name="Cardoso")

    reloaded = cm_module.ConfigManager(config_path=str(cm.config_path))
    assert user_display_name(reloaded.get_user(7)) == "Federico Cardoso"


# ── last_seen, and the cost of recording it ──


def test_the_first_contact_records_when_it_happened(cm):
    cm.register_pending(7, "cardosofede")

    assert cm.touch_user_identity(7, username="cardosofede") is True
    assert cm.get_user(7)["last_seen"] == pytest.approx(time.time(), abs=5)


def test_a_second_contact_in_the_same_minute_writes_nothing(cm):
    """`restricted` runs on every button press and each write is a full YAML
    dump; recording the exact second would rewrite config.yml a dozen times
    during one menu walk."""
    cm.register_pending(7, "cardosofede")
    cm.touch_user_identity(7, username="cardosofede")

    assert cm.touch_user_identity(7, username="cardosofede") is False


def test_a_contact_after_the_resolution_window_records_again(cm):
    cm.register_pending(7, "cardosofede")
    cm.touch_user_identity(7, username="cardosofede")
    stale = time.time() - cm_module.LAST_SEEN_RESOLUTION_SEC - 1
    cm.get_user(7)["last_seen"] = stale

    assert cm.touch_user_identity(7, username="cardosofede") is True
    assert cm.get_user(7)["last_seen"] > stale


def test_a_name_change_is_captured_even_inside_the_quiet_window(cm):
    """The throttle is on `last_seen` only — never on learning something new."""
    cm.register_pending(7, "cardosofede")
    cm.touch_user_identity(7, username="cardosofede")

    assert cm.touch_user_identity(7, first_name="Federico") is True
    assert cm.get_user(7)["first_name"] == "Federico"
