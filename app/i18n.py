"""Minimal i18n helper for the Stock Bot web UI.

Supported languages: EN (English), RU (Russian), TM (Turkmen).
Add new keys here as the UI grows.  The *en* dict is the authoritative
source of truth; missing keys in other languages fall back to English.
"""
from __future__ import annotations

TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        # Navigation
        "nav_admin": "Admin",
        # Unlock screen
        "unlock_title": "Stock Bot — Locked",
        "unlock_heading": "Site is locked",
        "unlock_prompt": "Enter the shared password to continue:",
        "unlock_password_placeholder": "Password",
        "unlock_submit": "Unlock",
        "unlock_error": "Incorrect password. Please try again.",
        # Admin / Settings page
        "settings_title": "Admin — Settings",
        "settings_heading": "Admin Settings",
        # Site lock section
        "settings_sitelock_heading": "Site Lock",
        "settings_sitelock_enable": "Enable site lock (require password to access the app)",
        "settings_sitelock_new_password": "New shared password (leave blank to keep current)",
        "settings_sitelock_session_hours": "Session duration (hours)",
        "settings_sitelock_logout_all": "Invalidate all existing sessions",
        "settings_sitelock_save": "Save site-lock settings",
        # Language section
        "settings_lang_heading": "Default Language",
        "settings_lang_label": "Default language for new visitors",
        "settings_lang_save": "Save language setting",
        # Theme section
        "settings_theme_heading": "Default Theme",
        "settings_theme_label": "Default theme for new visitors",
        "settings_theme_light": "Light",
        "settings_theme_dark": "Dark",
        "settings_theme_system": "System",
        "settings_theme_save": "Save theme setting",
        # Background section
        "settings_bg_heading": "Background Image",
        "settings_bg_enable": "Show background image",
        "settings_bg_size": "Background size",
        "settings_bg_size_cover": "Cover (fill screen)",
        "settings_bg_size_contain": "Contain (fit inside)",
        "settings_bg_overlay": "Overlay opacity (0 – 100, 0 = no overlay)",
        "settings_bg_upload": "Upload new background image (JPG/PNG)",
        "settings_bg_save": "Save background settings",
        # UI controls
        "theme_toggle_label": "Theme",
        "lang_toggle_label": "Language",
        "logout_label": "Logout",
        "saved_ok": "Settings saved.",
    },
    "ru": {
        # Navigation
        "nav_admin": "Админ",
        # Unlock screen
        "unlock_title": "Stock Bot — Заблокировано",
        "unlock_heading": "Сайт заблокирован",
        "unlock_prompt": "Введите общий пароль для продолжения:",
        "unlock_password_placeholder": "Пароль",
        "unlock_submit": "Разблокировать",
        "unlock_error": "Неверный пароль. Попробуйте ещё раз.",
        # Admin / Settings page
        "settings_title": "Админ — Настройки",
        "settings_heading": "Настройки",
        # Site lock section
        "settings_sitelock_heading": "Блокировка сайта",
        "settings_sitelock_enable": "Включить блокировку (требовать пароль для входа)",
        "settings_sitelock_new_password": "Новый пароль (оставьте пустым, чтобы не менять)",
        "settings_sitelock_session_hours": "Продолжительность сессии (часы)",
        "settings_sitelock_logout_all": "Завершить все активные сессии",
        "settings_sitelock_save": "Сохранить настройки блокировки",
        # Language section
        "settings_lang_heading": "Язык по умолчанию",
        "settings_lang_label": "Язык по умолчанию для новых посетителей",
        "settings_lang_save": "Сохранить язык",
        # Theme section
        "settings_theme_heading": "Тема по умолчанию",
        "settings_theme_label": "Тема по умолчанию для новых посетителей",
        "settings_theme_light": "Светлая",
        "settings_theme_dark": "Тёмная",
        "settings_theme_system": "Системная",
        "settings_theme_save": "Сохранить тему",
        # Background section
        "settings_bg_heading": "Фоновое изображение",
        "settings_bg_enable": "Показывать фоновое изображение",
        "settings_bg_size": "Размер фона",
        "settings_bg_size_cover": "Заполнить экран",
        "settings_bg_size_contain": "Вписать в экран",
        "settings_bg_overlay": "Прозрачность наложения (0 – 100, 0 = без наложения)",
        "settings_bg_upload": "Загрузить фон (JPG/PNG)",
        "settings_bg_save": "Сохранить настройки фона",
        # UI controls
        "theme_toggle_label": "Тема",
        "lang_toggle_label": "Язык",
        "logout_label": "Выйти",
        "saved_ok": "Настройки сохранены.",
    },
    "tm": {
        # Navigation
        "nav_admin": "Admin",
        # Unlock screen
        "unlock_title": "Stock Bot — Gulplandi",
        "unlock_heading": "Saýt gulplanan",
        "unlock_prompt": "Dowam etmek üçin umumy paroly giriziň:",
        "unlock_password_placeholder": "Parol",
        "unlock_submit": "Açmak",
        "unlock_error": "Nädogry parol. Täzeden synanyşyň.",
        # Admin / Settings page
        "settings_title": "Admin — Sazlamalar",
        "settings_heading": "Sazlamalar",
        # Site lock section
        "settings_sitelock_heading": "Saýt gulpy",
        "settings_sitelock_enable": "Saýt gulpuny işletmek (girişde parol talap etmek)",
        "settings_sitelock_new_password": "Täze parol (üýtgetmek islemedik bolsaňyz boş goýuň)",
        "settings_sitelock_session_hours": "Seans dowamlylygy (sagat)",
        "settings_sitelock_logout_all": "Ähli işjeň seanslary ýatyrmak",
        "settings_sitelock_save": "Gulp sazlamalaryny saklamak",
        # Language section
        "settings_lang_heading": "Deslapky dil",
        "settings_lang_label": "Täze myhmanlar üçin deslapky dil",
        "settings_lang_save": "Dil sazlamasyny saklamak",
        # Theme section
        "settings_theme_heading": "Deslapky tema",
        "settings_theme_label": "Täze myhmanlar üçin deslapky tema",
        "settings_theme_light": "Ýagty",
        "settings_theme_dark": "Garaňky",
        "settings_theme_system": "Ulgam",
        "settings_theme_save": "Tema sazlamasyny saklamak",
        # Background section
        "settings_bg_heading": "Fon suraty",
        "settings_bg_enable": "Fon suratyny görkezmek",
        "settings_bg_size": "Fon ölçegi",
        "settings_bg_size_cover": "Ekrany doldur",
        "settings_bg_size_contain": "Içine sygdyr",
        "settings_bg_overlay": "Üstüni örtme aýdyňlygy (0 – 100, 0 = örtmesiz)",
        "settings_bg_upload": "Täze fon ýükläň (JPG/PNG)",
        "settings_bg_save": "Fon sazlamalaryny saklamak",
        # UI controls
        "theme_toggle_label": "Tema",
        "lang_toggle_label": "Dil",
        "logout_label": "Çykmak",
        "saved_ok": "Sazlamalar saklandy.",
    },
}

SUPPORTED_LANGS = ("en", "ru", "tm")
_FALLBACK = "en"


def get_translations(lang: str) -> dict[str, str]:
    """Return translation dict for *lang*, filling missing keys from English."""
    lang = lang.lower() if lang else _FALLBACK
    if lang not in SUPPORTED_LANGS:
        lang = _FALLBACK
    base = dict(TRANSLATIONS[_FALLBACK])
    if lang != _FALLBACK:
        base.update(TRANSLATIONS[lang])
    return base
