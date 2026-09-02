from __future__ import annotations

import sys
from contextlib import AbstractContextManager
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from corewarden.tray import TrayController


class FakeMenuItem:
    def __init__(
        self,
        text: Any,
        action: Any,
        *,
        default: bool = False,
        enabled: bool = True,
    ) -> None:
        self.text = text
        self.action = action
        self.default = default
        self.enabled = enabled


class FakeMenu:
    def __init__(self, *items: FakeMenuItem) -> None:
        self.items = items


class FakeIcon:
    def __init__(self, name: str, image: Any, title: str, menu: FakeMenu) -> None:
        self.name = name
        self.image = image
        self.title = title
        self.menu = menu
        self.detached_runs = 0
        self.menu_updates = 0
        self.stops = 0

    def run_detached(self) -> None:
        self.detached_runs += 1

    def update_menu(self) -> None:
        self.menu_updates += 1

    def stop(self) -> None:
        self.stops += 1


class FakePystray:
    Menu = FakeMenu
    MenuItem = FakeMenuItem
    Icon = FakeIcon


class FakeOpenedImage(AbstractContextManager["FakeOpenedImage"]):
    def __init__(self, path: Path) -> None:
        self.path = path

    def __exit__(self, *args: object) -> None:
        return None

    def copy(self) -> str:
        return f"copied:{self.path.name}"


def test_tray_controller_owns_one_icon_and_routes_existing_actions() -> None:
    calls: list[str] = []
    monitoring = False
    controller = TrayController(
        Path("approved.png"),
        on_open=lambda: calls.append("open"),
        on_toggle_monitoring=lambda: calls.append("toggle"),
        on_quit=lambda: calls.append("quit"),
        monitoring_active=lambda: monitoring,
        pystray_module=FakePystray,
        image_loader=lambda path: f"image:{path.name}",
    )

    assert controller.start() is True
    assert controller.start() is False
    assert controller.running is True
    icon = controller._icon
    assert isinstance(icon, FakeIcon)
    assert icon.detached_runs == 1
    assert icon.image == "image:approved.png"
    assert icon.menu.items[1].text(icon.menu.items[1]) == "Monitoring: Stopped"
    assert icon.menu.items[2].text(icon.menu.items[2]) == "Start Monitoring"

    icon.menu.items[0].action(icon, icon.menu.items[0])
    icon.menu.items[2].action(icon, icon.menu.items[2])
    icon.menu.items[3].action(icon, icon.menu.items[3])
    assert calls == ["open", "toggle", "quit"]

    monitoring = True
    assert icon.menu.items[1].text(icon.menu.items[1]) == "Monitoring: Active"
    assert icon.menu.items[2].text(icon.menu.items[2]) == "Stop Monitoring"
    controller.refresh()
    assert icon.menu_updates == 1
    assert controller.stop() is True
    assert icon.stops == 1
    assert controller.stop() is False


def test_tray_lifecycle_alone_does_not_call_monitoring_or_provider() -> None:
    calls: list[str] = []
    controller = TrayController(
        Path("approved.png"),
        on_open=lambda: calls.append("open"),
        on_toggle_monitoring=lambda: calls.append("toggle"),
        on_quit=lambda: calls.append("quit"),
        monitoring_active=lambda: False,
        pystray_module=FakePystray,
        image_loader=lambda _path: object(),
    )

    controller.start()
    controller.refresh()
    controller.stop()

    assert calls == []


def test_tray_loads_runtime_dependencies_and_copies_approved_icon(monkeypatch: Any) -> None:
    pystray = ModuleType("pystray")
    pystray.Menu = FakeMenu
    pystray.MenuItem = FakeMenuItem
    pystray.Icon = FakeIcon
    pillow = ModuleType("PIL")
    pillow.Image = type("FakeImage", (), {"open": staticmethod(FakeOpenedImage)})
    monkeypatch.setitem(sys.modules, "pystray", pystray)
    monkeypatch.setitem(sys.modules, "PIL", pillow)
    controller = TrayController(
        Path("approved.png"),
        on_open=lambda: None,
        on_toggle_monitoring=lambda: None,
        on_quit=lambda: None,
        monitoring_active=lambda: False,
    )

    assert controller.start() is True
    assert controller._icon.image == "copied:approved.png"


def test_failed_tray_start_cleans_up_icon_for_safe_retry() -> None:
    class FailingIcon(FakeIcon):
        def run_detached(self) -> None:
            raise RuntimeError("synthetic tray startup failure")

    class FailingPystray(FakePystray):
        Icon = FailingIcon

    controller = TrayController(
        Path("approved.png"),
        on_open=lambda: None,
        on_toggle_monitoring=lambda: None,
        on_quit=lambda: None,
        monitoring_active=lambda: False,
        pystray_module=FailingPystray,
        image_loader=lambda _path: object(),
    )

    with pytest.raises(RuntimeError, match="synthetic tray startup failure"):
        controller.start()

    assert controller.running is False
