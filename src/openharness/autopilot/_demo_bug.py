def get_user_display_name(user: dict | None) -> str:
    return user["name"].upper()
