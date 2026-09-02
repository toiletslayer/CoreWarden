"""Small system-tray adapter with no Tk calls from tray threads."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any


class TrayController:
    """Own one pystray icon and delegate every action to marshaled callbacks."""

    def __init__(
        self,
        icon_path: Path,
        *,
        on_open: Callable[[], None],
        on_toggle_monitoring: Callable[[], None],
        on_quit: Callable[[], None],
        monitoring_active: Callable[[], bool],
        pystray_module: Any | None = None,
        image_loader: Callable[[Path], Any] | None = None,
    ) -> None:
        self.icon_path = icon_path
        self.on_open = on_open
        self.on_toggle_monitoring = on_toggle_monitoring
        self.on_quit = on_quit
        self.monitoring_active = monitoring_active
        self._pystray = pystray_module
        self._image_loader = image_loader
        self._icon: Any | None = None

    @property
    def running(self) -> bool:
        return self._icon is not None

    def _dependencies(self) -> tuple[Any, Callable[[Path], Any]]:
        if self._pystray is None:
            import pystray

            self._pystray = pystray
        if self._image_loader is None:
            from PIL import Image

            def load_image(path: Path) -> Any:
                with Image.open(path) as image:
                    return image.copy()

            self._image_loader = load_image
        return self._pystray, self._image_loader

    def start(self) -> bool:
        if self._icon is not None:
            return False
        pystray, image_loader = self._dependencies()
        menu = pystray.Menu(
            pystray.MenuItem("Open CoreWarden", lambda _icon, _item: self.on_open(), default=True),
            pystray.MenuItem(
                lambda _item: (
                    "Monitoring: Active" if self.monitoring_active() else "Monitoring: Stopped"
                ),
                None,
                enabled=False,
            ),
            pystray.MenuItem(
                lambda _item: "Stop Monitoring" if self.monitoring_active() else "Start Monitoring",
                lambda _icon, _item: self.on_toggle_monitoring(),
            ),
            pystray.MenuItem("Quit CoreWarden", lambda _icon, _item: self.on_quit()),
        )
        icon = pystray.Icon(
            "CoreWarden",
            image_loader(self.icon_path),
            "CoreWarden node monitoring",
            menu,
        )
        self._icon = icon
        try:
            icon.run_detached()
        except Exception:
            self._icon = None
            with suppress(Exception):
                icon.stop()
            raise
        return True

    def refresh(self) -> None:
        if self._icon is not None:
            self._icon.update_menu()

    def stop(self) -> bool:
        icon = self._icon
        if icon is None:
            return False
        self._icon = None
        icon.stop()
        return True
