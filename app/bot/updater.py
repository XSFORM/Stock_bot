"""
Self-update helper for the Telegram bot.

The bot's main menu has a 🔄 "Обновить" button that runs:
  git pull --ff-only       in BOT_UPDATE_REPO_PATH
  systemctl restart <svc>  for BOT_UPDATE_WEB_SERVICE

Everything is captured (stdout + stderr + exit code) and returned to the
admin as a chat message, so a non-technical user never has to SSH just
to pull a fix.

Scope:
  * Only triggers git pull (fast-forward only — no merges, no conflicts).
  * Only restarts the web systemd unit. The bot restarts itself at the
    end if its own files changed and BOT_UPDATE_RESTART_BOT=1.
  * Assumes the bot runs as root (common on single-tenant VPS) so
    `systemctl restart` works without a sudo password. If not, the
    output will show the sudo prompt and the admin can set up
    passwordless sudo for the relevant services.

Env overrides (all optional):
  BOT_UPDATE_REPO_PATH      — default /opt/stock_bot
  BOT_UPDATE_WEB_SERVICE    — default stockweb
  BOT_UPDATE_BOT_SERVICE    — default stockbot
  BOT_UPDATE_RESTART_BOT    — "1" to self-restart after pull (default "0")
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


_DEFAULT_REPO = "/opt/stock_bot"
_DEFAULT_WEB_SERVICE = "stockweb"
_DEFAULT_BOT_SERVICE = "stockbot"
_STEP_TIMEOUT_S = 120  # per-command budget


@dataclass
class _StepResult:
    label: str
    rc: int
    stdout: str
    stderr: str
    duration_s: float

    @property
    def ok(self) -> bool:
        return self.rc == 0


def _env(name: str, default: str) -> str:
    return (os.getenv(name) or default).strip() or default


def _run(cmd: list[str], *, cwd: str | None = None, label: str) -> _StepResult:
    """Run a command synchronously with a timeout, return a captured result."""
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True,
            timeout=_STEP_TIMEOUT_S, check=False,
        )
        return _StepResult(
            label=label, rc=proc.returncode,
            stdout=(proc.stdout or "").strip(),
            stderr=(proc.stderr or "").strip(),
            duration_s=time.monotonic() - t0,
        )
    except subprocess.TimeoutExpired:
        return _StepResult(
            label=label, rc=124,
            stdout="", stderr=f"timeout after {_STEP_TIMEOUT_S}s",
            duration_s=time.monotonic() - t0,
        )
    except FileNotFoundError as exc:
        return _StepResult(
            label=label, rc=127,
            stdout="", stderr=f"command not found: {exc}",
            duration_s=time.monotonic() - t0,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("updater: %s crashed", label)
        return _StepResult(
            label=label, rc=1,
            stdout="", stderr=f"{type(exc).__name__}: {exc}",
            duration_s=time.monotonic() - t0,
        )


def _trim(s: str, max_lines: int = 20) -> str:
    """Keep output under Telegram message limits — last N lines of each step."""
    if not s:
        return "(нет вывода)"
    lines = s.splitlines()
    if len(lines) <= max_lines:
        return s
    cut = lines[-max_lines:]
    return f"... (ещё {len(lines) - max_lines} строк)\n" + "\n".join(cut)


def _format_step(step: _StepResult) -> str:
    header = f"<b>{step.label}</b>  rc={step.rc}  ({step.duration_s:.1f}s)"
    body_parts: list[str] = []
    if step.stdout:
        body_parts.append(_trim(step.stdout))
    if step.stderr:
        body_parts.append(f"<i>stderr:</i>\n{_trim(step.stderr)}")
    if not body_parts:
        body_parts.append("(нет вывода)")
    body = "\n".join(body_parts)
    # HTML-escape and wrap body in <pre> so Telegram keeps the formatting.
    import html as _html
    return f"{header}\n<pre>{_html.escape(body)}</pre>"


def _bot_files_changed(pull_stdout: str) -> bool:
    """Rough check: did `git pull` touch any file under app/bot/?

    git pull's "Fast-forward" summary lists changed paths — if any starts
    with app/bot/ we assume the running bot's own code was updated.
    """
    return "app/bot/" in (pull_stdout or "")


def _blocking_update() -> tuple[str, bool]:
    """Run the full update sequence synchronously. Returns (message, should_restart_bot)."""
    repo = _env("BOT_UPDATE_REPO_PATH", _DEFAULT_REPO)
    web_service = _env("BOT_UPDATE_WEB_SERVICE", _DEFAULT_WEB_SERVICE)
    restart_bot = _env("BOT_UPDATE_RESTART_BOT", "0") == "1"

    if not Path(repo).is_dir():
        return (f"❌ Папка {shlex.quote(repo)} не существует.\n"
                f"Задай путь через env <code>BOT_UPDATE_REPO_PATH</code>.", False)

    steps: list[_StepResult] = []
    steps.append(_run(["git", "pull", "--ff-only"], cwd=repo, label="git pull"))

    # Only touch systemd if git pull succeeded — restarting on a failed
    # pull would just restart the old code with no changes.
    if steps[0].ok:
        steps.append(_run(
            ["systemctl", "restart", web_service],
            label=f"systemctl restart {web_service}",
        ))
        steps.append(_run(
            ["systemctl", "is-active", web_service],
            label=f"is-active {web_service}",
        ))

    bot_changed = restart_bot and _bot_files_changed(steps[0].stdout)

    header_bits = [f"<b>🔄 Обновление</b>  <code>{shlex.quote(repo)}</code>"]
    if all(s.ok for s in steps):
        header_bits.append("✅ Готово")
    else:
        header_bits.append("⚠️ Есть ошибки")
    if bot_changed:
        header_bits.append("♻️ Бот перезапустится через 3с (код бота изменился)")
    elif not restart_bot and "app/bot" in (steps[0].stdout or ""):
        header_bits.append("ℹ️ Код бота изменился — перезапусти вручную: "
                           "<code>systemctl restart stockbot</code>")

    body = "\n\n".join(_format_step(s) for s in steps)
    message = header_bits[0] + "\n" + "  ".join(header_bits[1:]) + "\n\n" + body
    return message, bot_changed


async def run_update() -> tuple[str, bool]:
    """Async wrapper — runs the blocking update in a thread so the event loop stays responsive."""
    return await asyncio.to_thread(_blocking_update)


async def schedule_bot_self_restart(delay_s: float = 3.0) -> None:
    """After sending the result message, kick the bot's systemd unit so new code loads.

    Uses `systemctl restart` with a delay so the already-sent message is
    delivered first. Fire-and-forget: we return immediately.
    """
    bot_service = _env("BOT_UPDATE_BOT_SERVICE", _DEFAULT_BOT_SERVICE)

    async def _delayed() -> None:
        try:
            await asyncio.sleep(delay_s)
            # Non-blocking exec — bot will be killed by systemd before this returns.
            subprocess.Popen(["systemctl", "restart", bot_service])
        except Exception:
            logger.exception("delayed bot restart failed")

    asyncio.create_task(_delayed())
