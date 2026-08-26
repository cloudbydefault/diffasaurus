from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path

from diffasaurus.core.settings import (
    load_settings,
    managed_updates_enabled,
    updates_enabled,
)

logger = logging.getLogger(__name__)

DEFAULT_PREVIEW_FEED_URL = "https://cloudbydefault.github.io/diffasaurus/appcast-preview.xml"
TRANSIENT_INSTALL_MESSAGE = (
    "Move Diffasaurus to Applications before updating."
)
MANAGED_UPDATES_MESSAGE = "Updates managed by your organization"


def is_packaged_macos_app() -> bool:
    return sys.platform == "darwin" and bool(getattr(sys, "frozen", False))


def _app_bundle_path() -> Path | None:
    if not is_packaged_macos_app():
        return None
    executable = Path(sys.executable).resolve()
    if executable.name != "Diffasaurus":
        return None
    bundle = executable.parent.parent.parent
    if bundle.suffix != ".app":
        return None
    return bundle


def running_from_transient_install_location() -> bool:
    bundle = _app_bundle_path()
    if bundle is None:
        return False
    return str(bundle).startswith("/Volumes/")


def sparkle_framework_path() -> Path | None:
    bundle = _app_bundle_path()
    if bundle is None:
        return None
    framework = bundle / "Contents" / "Frameworks" / "Sparkle.framework"
    return framework if framework.is_dir() else None


def _info_plist_value(key: str) -> str:
    try:
        from Foundation import NSBundle

        value = NSBundle.mainBundle().objectForInfoDictionaryKey_(key)
        return str(value or "").strip()
    except Exception:
        return ""


class MacOsUpdaterService:
    """Thin PyObjC bridge around Sparkle's standard updater controller."""

    def __init__(
        self,
        *,
        save_session_callback: Callable[[], None] | None = None,
    ):
        self._save_session_callback = save_session_callback
        self._controller = None
        self._updater_delegate = None
        self._disabled_reason = ""
        self._initialize()

    @property
    def disabled_reason(self) -> str:
        return self._disabled_reason

    def is_managed(self) -> bool:
        return managed_updates_enabled()

    def is_available(self) -> bool:
        return self._controller is not None and not self._disabled_reason

    def _sparkle_updater(self):
        if self._controller is None:
            return None
        try:
            return self._controller.updater()
        except Exception:
            return None

    def can_check_for_updates(self) -> bool:
        if not self.is_available():
            return False
        if running_from_transient_install_location():
            return False
        updater = self._sparkle_updater()
        if updater is None:
            return False
        try:
            return bool(updater.canCheckForUpdates())
        except Exception:
            return False

    def status_message(self) -> str:
        if managed_updates_enabled() or not updates_enabled():
            return MANAGED_UPDATES_MESSAGE
        if not is_packaged_macos_app():
            return ""
        if self._disabled_reason:
            return self._disabled_reason
        if running_from_transient_install_location():
            return TRANSIENT_INSTALL_MESSAGE
        return ""

    def check_for_updates(self) -> tuple[bool, str]:
        if managed_updates_enabled() or not updates_enabled():
            return False, MANAGED_UPDATES_MESSAGE
        if not is_packaged_macos_app():
            return False, "Updates are only available in the packaged macOS app."
        if running_from_transient_install_location():
            return False, TRANSIENT_INSTALL_MESSAGE
        if self._controller is None:
            return False, self._disabled_reason or "Updates are unavailable."
        try:
            if not self.can_check_for_updates():
                return False, "Updates are not ready yet. Try again in a moment."
            self._controller.checkForUpdates_(None)
            return True, ""
        except Exception as exc:
            logger.exception("Sparkle checkForUpdates failed")
            return False, f"Update check failed: {exc}"

    def _initialize(self) -> None:
        if sys.platform != "darwin":
            self._disabled_reason = "Updates are only supported on macOS."
            return
        if not is_packaged_macos_app():
            self._disabled_reason = "Source builds do not self-update."
            return
        if managed_updates_enabled() or not updates_enabled():
            self._disabled_reason = MANAGED_UPDATES_MESSAGE
            return
        framework = sparkle_framework_path()
        if framework is None:
            self._disabled_reason = "Sparkle.framework is not bundled."
            logger.warning("Sparkle.framework missing from app bundle.")
            return
        public_key = _info_plist_value("SUPublicEDKey")
        feed_url = _info_plist_value("SUFeedURL")
        if not public_key or not feed_url:
            self._disabled_reason = "Update signing metadata is not configured."
            logger.warning(
                "Sparkle disabled: SUPublicEDKey or SUFeedURL missing from Info.plist."
            )
            return
        try:
            import objc
            from Foundation import NSObject
        except ImportError:
            self._disabled_reason = "PyObjC is not available."
            logger.warning("PyObjC import failed; macOS updater disabled.")
            return

        try:
            objc.loadBundle(
                "Sparkle",
                globals(),
                bundle_path=str(framework),
            )
            controller_class = objc.lookUpClass("SPUStandardUpdaterController")
        except Exception as exc:
            self._disabled_reason = "Sparkle.framework could not be loaded."
            logger.exception("Failed to load Sparkle.framework: %s", exc)
            return

        save_callback = self._save_session_callback

        class UpdaterDelegate(NSObject):
            def feedURLStringForUpdater_(self, updater):  # noqa: N802
                if os.environ.get("DIFFASAURUS_UPDATER_TEST_MODE") == "1":
                    override = os.environ.get("DIFFASAURUS_UPDATE_FEED_URL", "").strip()
                    if override:
                        return override
                return None

            def updater_willInstallUpdate_(self, updater, item):  # noqa: N802
                if save_callback is not None:
                    try:
                        save_callback()
                    except Exception:
                        logger.exception("Failed to save pre-update session state")

        self._updater_delegate = UpdaterDelegate.alloc().init()
        try:
            self._controller = controller_class.alloc().initWithStartingUpdater_updaterDelegate_userDriverDelegate_(
                True,
                self._updater_delegate,
                None,
            )
            sparkle_updater = self._sparkle_updater()
            can_check = bool(sparkle_updater.canCheckForUpdates()) if sparkle_updater is not None else False
            logger.info(
                "Sparkle updater initialized: framework=%s feed_configured=%s can_check_for_updates=%s",
                framework.name,
                bool(feed_url),
                can_check,
            )
        except Exception as exc:
            self._controller = None
            self._updater_delegate = None
            self._disabled_reason = "Sparkle updater could not start."
            logger.exception("Sparkle controller init failed: %s", exc)


def create_macos_updater_service(
    *,
    save_session_callback: Callable[[], None] | None = None,
) -> MacOsUpdaterService:
    return MacOsUpdaterService(save_session_callback=save_session_callback)
