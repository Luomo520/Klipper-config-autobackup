from __future__ import annotations

import asyncio
import pathlib
import unittest
from contextlib import ExitStack
from tempfile import TemporaryDirectory
from unittest.mock import patch

from moonraker.components import cloud_backup_web


class FakeLocator:
    def __init__(self, count: int = 1, text: str = "") -> None:
        self._count = count
        self._text = text
        self.first = self

    async def count(self) -> int:
        return self._count

    async def is_visible(self) -> bool:
        return self._count > 0

    async def wait_for(self, **kwargs: object) -> None:
        return None

    async def set_input_files(self, path: str) -> None:
        return None

    async def inner_text(self) -> str:
        return self._text


class FakePage:
    def __init__(self, body_text: str, login_count: int = 0) -> None:
        self.body_text = body_text
        self.login_count = login_count

    def locator(self, selector: str) -> FakeLocator:
        if selector == cloud_backup_web.LOGIN_BUTTON:
            return FakeLocator(self.login_count)
        if selector == cloud_backup_web.FILE_INPUT:
            return FakeLocator(1)
        if selector == "body":
            return FakeLocator(1, self.body_text)
        raise AssertionError(f"Unexpected selector: {selector}")

    async def goto(self, *args: object, **kwargs: object) -> None:
        return None

    async def wait_for_timeout(self, timeout: int) -> None:
        return None


class FakeClock:
    def __init__(self, values: list[float]) -> None:
        self.values = iter(values)

    def monotonic(self) -> float:
        return next(self.values)


class CloudBackupWebTest(unittest.TestCase):
    def test_baidu_authorization_requires_upload_control(self) -> None:
        page = FakePage("")
        self.assertTrue(asyncio.run(cloud_backup_web._is_baidu_authorized(page)))

        page.locator = lambda selector: (
            FakeLocator(0) if selector == cloud_backup_web.FILE_INPUT
            else FakeLocator(0)
        )
        self.assertFalse(asyncio.run(cloud_backup_web._is_baidu_authorized(page)))

    def test_baidu_upload_does_not_succeed_without_remote_size_match(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            archive = pathlib.Path(temp_dir).joinpath("printer.tar.gz")
            archive.write_bytes(b"backup")
            page = FakePage(archive.name)
            emitted = []

            async def ensure_directory(page: object, remote_dir: str) -> None:
                return None

            async def remote_size(
                page: object, remote_dir: str, filename: str
            ) -> None:
                return None

            with ExitStack() as stack:
                stack.enter_context(patch.object(
                    cloud_backup_web,
                    "_ensure_remote_directory",
                    new=ensure_directory,
                ))
                stack.enter_context(patch.object(
                    cloud_backup_web,
                    "_baidu_remote_file_size",
                    new=remote_size,
                ))
                stack.enter_context(patch.object(
                    cloud_backup_web,
                    "_emit",
                    side_effect=lambda state, **values: emitted.append(state),
                ))
                stack.enter_context(patch.object(
                    cloud_backup_web,
                    "time",
                    new=FakeClock([0.0, 0.0, 1.0, 6.0, 601.0]),
                ))
                with self.assertRaisesRegex(RuntimeError, "Timed out"):
                    asyncio.run(
                        cloud_backup_web._upload_baidu(page, archive, "/backup")
                    )

            self.assertEqual(emitted, ["uploading"])

    def test_baidu_upload_succeeds_after_remote_size_match(self) -> None:
        with TemporaryDirectory() as temp_dir:
            archive = pathlib.Path(temp_dir).joinpath("printer.tar.gz")
            archive.write_bytes(b"backup")
            page = FakePage(archive.name)
            emitted = []

            async def ensure_directory(page: object, remote_dir: str) -> None:
                return None

            async def remote_size(
                page: object, remote_dir: str, filename: str
            ) -> int:
                return archive.stat().st_size

            with ExitStack() as stack:
                stack.enter_context(patch.object(
                    cloud_backup_web,
                    "_ensure_remote_directory",
                    new=ensure_directory,
                ))
                stack.enter_context(patch.object(
                    cloud_backup_web,
                    "_baidu_remote_file_size",
                    new=remote_size,
                ))
                stack.enter_context(patch.object(
                    cloud_backup_web,
                    "_emit",
                    side_effect=lambda state, **values: emitted.append(state),
                ))
                stack.enter_context(patch.object(
                    cloud_backup_web,
                    "time",
                    new=FakeClock([0.0, 0.0, 1.0, 6.0]),
                ))
                asyncio.run(
                    cloud_backup_web._upload_baidu(page, archive, "/backup")
                )

            self.assertEqual(emitted, ["uploading", "uploaded"])


if __name__ == "__main__":
    unittest.main()
