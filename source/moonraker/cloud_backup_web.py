# Web-session worker for the cloud_backup component
#
# This process intentionally isolates Playwright and browser state from Moonraker.

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import pathlib
import sys
import time
from typing import Any, Dict, Optional
from urllib.parse import quote


PAN_URL = "https://pan.baidu.com/disk/main"
QUARK_URL = "https://pan.quark.cn/"
LOGIN_BUTTON = "button.bd-login-button__wrapper"
USERNAME_INPUT = 'input[name="userName"]'
PASSWORD_INPUT = 'input[name="password"][type="password"]'
AGREEMENT_INPUT = 'input[name="isAgree"]'
CAPTCHA_INPUT = 'input[name="verifyCode"]'
SMS_CODE_INPUT = 'input[id$="__smsVerifyCode"]'
FILE_INPUT = (
    'a.nd-upload-button > form.nd-h5-form > '
    'input[type="file"][name="html5uploader"]:not([webkitdirectory])'
)
QUARK_QR_CONTAINER = ".qrcode-container"
QUARK_FILE_INPUT = '#ListHeader-file[type="file"]'
QUARK_NEW_FOLDER_BUTTON = "button.btn-create-folder"
QUARK_FOLDER_EDIT = "input.input-edit"
QUARK_PHONE_IFRAME = "iframe.mobile-container"
QUARK_PHONE_INPUT = 'input[name="login_name"]'
QUARK_SMS_INPUT = 'input[placeholder="短信验证码"]'


def _emit(state: str, **values: Any) -> None:
    payload = {"state": state, **values}
    print(json.dumps(payload, ensure_ascii=False), flush=True)


async def _read_command() -> Dict[str, Any]:
    loop = asyncio.get_running_loop()
    line = await loop.run_in_executor(None, sys.stdin.readline)
    if not line:
        raise RuntimeError("Moonraker closed the web-login session")
    value = json.loads(line)
    if not isinstance(value, dict):
        raise RuntimeError("Invalid worker command")
    return value


async def _challenge(page: Any, challenge_type: str, message: str) -> str:
    screenshot = await page.screenshot(type="png")
    _emit(
        "verification_required",
        challenge_type=challenge_type,
        message=message,
        screenshot_data=(
            "data:image/png;base64," + base64.b64encode(screenshot).decode("ascii")
        ),
    )
    command = await _read_command()
    code = str(command.get("verification_code", "")).strip()
    if not 4 <= len(code) <= 12:
        raise RuntimeError("Verification code must contain 4-12 characters")
    return code


async def _is_baidu_authorized(page: Any) -> bool:
    login_button = page.locator(LOGIN_BUTTON)
    if await login_button.count() > 0 and await login_button.first.is_visible():
        return False
    return await page.locator(FILE_INPUT).count() == 1


async def _ensure_login(
    page: Any, username: str, password: str, interactive: bool
) -> bool:
    await page.goto(PAN_URL, wait_until="domcontentloaded", timeout=60_000)
    await page.wait_for_timeout(1500)
    if await _is_baidu_authorized(page):
        return True
    if not username or not password:
        return False

    login_button = page.locator(LOGIN_BUTTON)
    if await login_button.count() != 1:
        raise RuntimeError("Baidu login button was not found")
    await login_button.click()
    account_tab = page.get_by_text("账号登录", exact=True)
    await account_tab.wait_for(state="visible", timeout=20_000)
    await account_tab.click()

    await page.locator(USERNAME_INPUT).fill(username)
    await page.locator(PASSWORD_INPUT).fill(password)
    agreement = page.locator(AGREEMENT_INPUT)
    if await agreement.count() == 1 and not await agreement.is_checked():
        await agreement.check(force=True)
    await page.locator('input[type="submit"][id$="__submit"]').click()

    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        await page.wait_for_timeout(1000)
        if await _is_baidu_authorized(page):
            return True

        captcha = page.locator(CAPTCHA_INPUT)
        if await captcha.count() == 1 and await captcha.is_visible():
            if not interactive:
                return False
            code = await _challenge(page, "captcha", "请输入百度页面显示的验证码")
            await captcha.fill(code)
            await page.locator('input[type="submit"][id$="__submit"]').click()
            continue

        sms_code = page.locator(SMS_CODE_INPUT)
        if await sms_code.count() == 1 and await sms_code.is_visible():
            if not interactive:
                return False
            code = await _challenge(page, "sms", "请输入百度发送的短信验证码")
            await sms_code.fill(code)
            submit = page.locator('input[type="submit"][id$="__smsSubmit"]')
            if await submit.count() == 1:
                await submit.click()
            continue

        body_text = await page.locator("body").inner_text()
        for marker in ("帐号或密码错误", "账号或密码错误", "密码错误", "登录失败"):
            if marker in body_text:
                raise RuntimeError(marker)
        if any(marker in body_text for marker in ("安全验证", "请完成验证")):
            screenshot = await page.screenshot(type="png")
            _emit(
                "manual_verification",
                challenge_type="interactive",
                message="百度要求完成交互式安全验证，请稍后重试或改用扫码登录",
                screenshot_data=(
                    "data:image/png;base64," +
                    base64.b64encode(screenshot).decode("ascii")
                ),
            )
            return False
    raise RuntimeError("Timed out while waiting for Baidu login")


async def _find_bdstoken(page: Any) -> str:
    return str(await page.evaluate(
        r"""() => {
            const direct = [
                window.yunData && window.yunData.MYBDSTOKEN,
                window.yunData && window.yunData.bdstoken,
                window.locals && window.locals.get && window.locals.get('bdstoken')
            ].find(value => typeof value === 'string' && value.length > 5);
            if (direct) return direct;
            const match = document.documentElement.innerHTML.match(
                /[\"']bdstoken[\"']\s*:\s*[\"']([^\"']+)[\"']/
            );
            return match ? match[1] : '';
        }"""
    ))


async def _ensure_remote_directory(page: Any, remote_dir: str) -> None:
    token = await _find_bdstoken(page)
    if not token:
        raise RuntimeError("Unable to read the Baidu web-session token")
    parts = [part for part in remote_dir.split("/") if part]
    current = ""
    for part in parts:
        current += "/" + part
        result = await page.evaluate(
            """async ({path, token}) => {
                const body = new URLSearchParams({
                    path, isdir: '1', size: '', block_list: '[]'
                });
                const response = await fetch(
                    '/api/create?a=commit&bdstoken=' + encodeURIComponent(token) +
                    '&clienttype=0&web=1',
                    {
                        method: 'POST',
                        credentials: 'include',
                        headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                        body: body.toString()
                    }
                );
                return await response.json();
            }""",
            {"path": current, "token": token},
        )
        errno = result.get("errno") if isinstance(result, dict) else None
        if errno not in (0, -8, "0", "-8"):
            raise RuntimeError(f"Baidu could not create remote folder (errno={errno})")


async def _baidu_remote_file_size(
    page: Any, remote_dir: str, filename: str
) -> Optional[int]:
    token = await _find_bdstoken(page)
    if not token:
        raise RuntimeError("Unable to read the Baidu web-session token")
    result = await page.evaluate(
        """async ({dir, filename, token}) => {
            const query = new URLSearchParams({
                order: 'time', desc: '1', showempty: '0', web: '1',
                page: '1', num: '1000', dir, bdstoken: token
            });
            const response = await fetch('/api/list?' + query.toString(), {
                credentials: 'include'
            });
            const payload = await response.json();
            const entries = Array.isArray(payload.list) ? payload.list : [];
            const match = entries.find(item => item.server_filename === filename);
            return {
                errno: payload.errno,
                size: match && Number.isFinite(Number(match.size))
                    ? Number(match.size) : null
            };
        }""",
        {"dir": remote_dir, "filename": filename, "token": token},
    )
    if not isinstance(result, dict):
        raise RuntimeError("Baidu returned an invalid remote file listing")
    if result.get("errno") not in (0, "0", None):
        raise RuntimeError(
            f"Baidu could not verify the uploaded file (errno={result.get('errno')})"
        )
    size = result.get("size")
    return int(size) if isinstance(size, (int, float)) else None


async def _upload_baidu(
    page: Any, archive: pathlib.Path, remote_dir: str
) -> None:
    await _ensure_remote_directory(page, remote_dir)
    target_url = f"{PAN_URL}#/index?category=all&path={quote(remote_dir, safe='')}"
    await page.goto(target_url, wait_until="domcontentloaded", timeout=60_000)
    file_input = page.locator(FILE_INPUT)
    await file_input.first.wait_for(state="attached", timeout=30_000)
    if await file_input.count() != 1:
        raise RuntimeError("Baidu upload control is unavailable or ambiguous")
    _emit("uploading", progress=25, message="正在通过百度网页上传")
    await file_input.first.set_input_files(str(archive))

    started_at = time.monotonic()
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        await page.wait_for_timeout(1500)
        body_text = await page.locator("body").inner_text()
        if "上传失败" in body_text:
            raise RuntimeError("Baidu web upload failed")
        transfer_pending = any(
            marker in body_text for marker in ("上传中", "等待上传", "正在上传")
        )
        should_verify = (
            archive.name in body_text and
            (
                "上传完成" in body_text or
                (time.monotonic() - started_at > 5 and not transfer_pending)
            )
        )
        if should_verify:
            remote_size = await _baidu_remote_file_size(
                page, remote_dir, archive.name
            )
            if remote_size == archive.stat().st_size:
                _emit("uploaded", remote_path=f"{remote_dir}/{archive.name}")
                return
    raise RuntimeError("Timed out while waiting for Baidu web upload")


async def _visible(locator: Any) -> bool:
    return await locator.count() > 0 and await locator.first.is_visible()


async def _is_quark_authorized(page: Any) -> bool:
    qr = page.locator(QUARK_QR_CONTAINER)
    if await _visible(qr):
        return False
    body_text = await page.locator("body").inner_text()
    if "请使用 夸克网盘APP 扫码登录" in body_text:
        return False
    return await page.locator(QUARK_FILE_INPUT).count() == 1


async def _quark_qr_screenshot(page: Any) -> str:
    qr = page.locator(QUARK_QR_CONTAINER)
    await qr.first.wait_for(state="visible", timeout=20_000)
    screenshot = await qr.first.screenshot(type="png")
    return "data:image/png;base64," + base64.b64encode(screenshot).decode("ascii")


async def _ensure_quark_qr_login(page: Any, interactive: bool) -> bool:
    await page.goto(QUARK_URL, wait_until="domcontentloaded", timeout=60_000)
    await page.wait_for_timeout(1500)
    if await _is_quark_authorized(page):
        return True
    if not interactive:
        return False

    deadline = time.monotonic() + 300
    next_qr_refresh = 0.0
    while time.monotonic() < deadline:
        if await _is_quark_authorized(page):
            return True
        if time.monotonic() >= next_qr_refresh:
            _emit(
                "verification_required",
                challenge_type="qr",
                message="请使用夸克网盘APP扫码登录",
                screenshot_data=await _quark_qr_screenshot(page),
            )
            next_qr_refresh = time.monotonic() + 15
        await page.wait_for_timeout(1000)
    raise RuntimeError("Timed out while waiting for Quark QR login")


async def _quark_phone_frame(page: Any) -> Any:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        for frame in page.frames:
            if await frame.locator(QUARK_PHONE_INPUT).count() == 1:
                return frame
        await page.wait_for_timeout(250)
    raise RuntimeError("Quark phone login form was not found")


async def _ensure_quark_phone_login(
    page: Any, phone: str, interactive: bool
) -> bool:
    await page.goto(QUARK_URL, wait_until="domcontentloaded", timeout=60_000)
    await page.wait_for_timeout(1500)
    if await _is_quark_authorized(page):
        return True
    if not interactive or not phone:
        return False

    phone_login = page.get_by_text("手机登录", exact=True)
    if await phone_login.count() != 1:
        raise RuntimeError("Quark phone login entry was not found")
    await phone_login.click()
    frame = await _quark_phone_frame(page)
    phone_input = frame.locator(QUARK_PHONE_INPUT)
    await phone_input.fill(phone)
    request_code = frame.get_by_text("获取短信验证码", exact=True)
    if await request_code.count() != 1:
        raise RuntimeError("Quark SMS request control was not found")
    await request_code.click()
    await page.wait_for_timeout(1000)

    frame_text = await frame.locator("body").inner_text()
    if any(marker in frame_text for marker in ("滑动", "安全验证", "完成验证")):
        screenshot = await page.screenshot(type="png")
        _emit(
            "manual_verification",
            challenge_type="interactive",
            message="夸克要求完成交互式安全验证，请改用扫码登录",
            screenshot_data=(
                "data:image/png;base64," +
                base64.b64encode(screenshot).decode("ascii")
            ),
        )
        return False

    sms_input = frame.locator(QUARK_SMS_INPUT)
    if await sms_input.count() != 1:
        raise RuntimeError("Quark SMS verification field was not found")
    code = await _challenge(page, "sms", "请输入夸克发送的短信验证码")
    await sms_input.fill(code)
    login_button = frame.get_by_role("button", name="登录", exact=True)
    if await login_button.count() != 1:
        raise RuntimeError("Quark login button was not found")
    await login_button.click()

    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        await page.wait_for_timeout(1000)
        if await _is_quark_authorized(page):
            return True
        frame_text = await frame.locator("body").inner_text()
        for marker in ("验证码错误", "验证码已失效", "登录失败"):
            if marker in frame_text:
                raise RuntimeError(marker)
    raise RuntimeError("Timed out while waiting for Quark phone login")


async def _open_quark_remote_directory(page: Any, remote_dir: str) -> None:
    parts = [part for part in remote_dir.strip("/").split("/") if part]
    for part in parts:
        folder = page.get_by_title(part, exact=True)
        count = await folder.count()
        if count == 0:
            create_button = page.locator(QUARK_NEW_FOLDER_BUTTON)
            if await create_button.count() != 1:
                raise RuntimeError("Quark new-folder control is unavailable")
            await create_button.click()
            editor = page.locator(QUARK_FOLDER_EDIT)
            await editor.wait_for(state="visible", timeout=10_000)
            if await editor.count() != 1:
                raise RuntimeError("Quark folder-name editor is ambiguous")
            await editor.fill(part)
            await editor.press("Enter")
            folder = page.get_by_title(part, exact=True)
            await folder.wait_for(state="visible", timeout=15_000)
            count = await folder.count()
        if count != 1:
            raise RuntimeError(f"Quark folder is unavailable or ambiguous: {part}")
        previous_url = page.url
        await folder.dblclick()
        deadline = time.monotonic() + 15
        while page.url == previous_url and time.monotonic() < deadline:
            await page.wait_for_timeout(250)
        if page.url == previous_url:
            raise RuntimeError(f"Quark could not open remote folder: {part}")
        await page.locator(QUARK_FILE_INPUT).wait_for(
            state="attached", timeout=15_000
        )


async def _quark_remote_file_exists(
    page: Any, archive: pathlib.Path, remote_dir: str
) -> bool:
    verification_page = await page.context.new_page()
    try:
        await verification_page.goto(
            QUARK_URL, wait_until="domcontentloaded", timeout=60_000
        )
        await verification_page.wait_for_timeout(1500)
        if not await _is_quark_authorized(verification_page):
            return False
        await _open_quark_remote_directory(verification_page, remote_dir)
        uploaded_file = verification_page.get_by_title(archive.name, exact=True)
        if await uploaded_file.count() != 1:
            return False
        return await uploaded_file.is_visible()
    finally:
        await verification_page.close()


async def _upload_quark(
    page: Any, archive: pathlib.Path, remote_dir: str
) -> None:
    await page.goto(QUARK_URL, wait_until="domcontentloaded", timeout=60_000)
    await page.wait_for_timeout(1500)
    if not await _is_quark_authorized(page):
        raise RuntimeError("Quark web session has expired; log in again")
    body_text = await page.locator("body").inner_text()
    if "账号涉嫌违规已被封禁" in body_text:
        raise RuntimeError("Quark account is restricted and cannot upload files")

    await _open_quark_remote_directory(page, remote_dir)
    file_input = page.locator(QUARK_FILE_INPUT)
    await file_input.first.wait_for(state="attached", timeout=30_000)
    if await file_input.count() != 1:
        raise RuntimeError("Quark upload control is unavailable or ambiguous")
    _emit("uploading", progress=25, message="正在通过夸克网页上传")
    await file_input.first.set_input_files(str(archive))

    started_at = time.monotonic()
    deadline = started_at + 600
    while time.monotonic() < deadline:
        await page.wait_for_timeout(1500)
        body_text = await page.locator("body").inner_text()
        if any(marker in body_text for marker in ("上传失败", "上传错误")):
            raise RuntimeError("Quark web upload failed")
        transfer_pending = any(
            marker in body_text for marker in ("上传中", "等待上传", "正在上传")
        )
        should_verify = (
            archive.name in body_text and
            (
                "上传完成" in body_text or
                (time.monotonic() - started_at > 5 and not transfer_pending)
            )
        )
        if should_verify and await _quark_remote_file_exists(
            page, archive, remote_dir
        ):
            remote_path = f"{remote_dir.rstrip('/')}/{archive.name}"
            _emit("uploaded", remote_path=remote_path)
            return
    raise RuntimeError("Timed out while waiting for Quark web upload")


async def _run(
    action: str, provider: str, browser_executable: Optional[str]
) -> None:
    request = await _read_command()
    profile_dir = pathlib.Path(str(request["profile_dir"])).resolve()
    profile_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    profile_dir.chmod(0o700)

    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        launch_args: Dict[str, Any] = {
            "headless": True,
            "args": ["--disable-dev-shm-usage", "--no-first-run"],
        }
        if browser_executable:
            launch_args["executable_path"] = browser_executable
        context = await playwright.chromium.launch_persistent_context(
            str(profile_dir), **launch_args
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            username = str(request.get("username", ""))
            password = str(request.get("password", ""))
            authorized = await _ensure_login(
                page, username, password, action == "login"
            )
            if not authorized:
                if action == "upload":
                    raise RuntimeError(
                        "Baidu web session has expired; log in again"
                    )
                return
            if action == "login":
                _emit("authorized", message="百度网页登录成功")
                return
            archive = pathlib.Path(str(request["archive_path"])).resolve()
            if not archive.is_file():
                raise RuntimeError("Backup archive does not exist")
            await _upload_baidu(page, archive, str(request["remote_dir"]))
        finally:
            await context.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("login", "upload"))
    parser.add_argument("--provider", choices=("baidu",), default="baidu")
    parser.add_argument("--browser-executable")
    args = parser.parse_args()
    try:
        asyncio.run(_run(args.action, args.provider, args.browser_executable))
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("playwright"):
            _emit(
                "environment_missing",
                message="未安装 Playwright，请安装后执行 playwright install chromium",
            )
        else:
            _emit("error", message="Web login dependency is missing")
        raise SystemExit(2)
    except Exception as exc:
        _emit("error", message=str(exc))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
