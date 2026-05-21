from openharness.autopilot._demo_bug import get_user_display_name


def test_get_user_display_name_normal():
    """正常用户应返回大写名称."""
    assert get_user_display_name({"name": "alice"}) == "ALICE"


def test_get_user_display_name_none():
    """传入 None 应返回默认值 'Anonymous' 而不崩溃."""
    assert get_user_display_name(None) == "Anonymous"
