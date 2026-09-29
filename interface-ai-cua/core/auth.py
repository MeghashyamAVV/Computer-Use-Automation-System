import os


class MissingCredentialsError(RuntimeError):
    pass


def ensure_logged_in(page, base_url: str) -> None:
    username = os.environ.get("CU_CONSOLE_USERNAME")
    password = os.environ.get("CU_CONSOLE_PASSWORD")
    if not username or not password:
        raise MissingCredentialsError(
            "CU_CONSOLE_USERNAME / CU_CONSOLE_PASSWORD are not set. The mock app accepts "
            "any non-empty demo values -- see .env.example."
        )
    page.goto(f"{base_url}/login")
    page.get_by_label("Username").fill(username)
    page.get_by_label("Password").fill(password)
    page.get_by_role("button", name="Sign In").click()
    page.wait_for_load_state("networkidle", timeout=8000)
