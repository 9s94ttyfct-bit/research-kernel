import pytest

from rk.canon import canonical_json, sha256_hex
from rk.config import Settings, load_settings, rk_home, save_settings
from rk.ids import is_id, new_id


def test_settings_roundtrip_and_mode(tmp_home):
    p = save_settings(Settings(email="a@b.org", openalex_key="k"))
    assert p == rk_home() / "config.toml"
    assert oct(p.stat().st_mode & 0o777) == "0o600"
    s = load_settings()
    assert s.openalex_key == "k" and s.email == "a@b.org" and s.s2_key is None


def test_load_missing_config_is_default(tmp_home):
    assert load_settings() == Settings()


def test_new_id_format():
    i = new_id("src")
    assert i.startswith("src_") and len(i) == 4 + 26 and is_id(i, "src")
    assert not is_id(i, "nte")
    assert not is_id("src_short")
    with pytest.raises(ValueError):
        new_id("zzz")


def test_canonical_json_stable():
    assert canonical_json({"b": 1, "a": "é"}) == '{"a":"é","b":1}'
    assert sha256_hex("x") == sha256_hex(b"x")
