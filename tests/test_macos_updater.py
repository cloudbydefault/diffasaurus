import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from diffasaurus.ui.macos_updater import (
    MANAGED_UPDATES_MESSAGE,
    MacOsUpdaterService,
    create_macos_updater_service,
    is_packaged_macos_app,
    running_from_transient_install_location,
)


class MacOsUpdaterTests(unittest.TestCase):
    def test_unsupported_platform_is_unavailable(self):
        with patch.object(sys, "platform", "linux"):
            service = create_macos_updater_service()
            self.assertFalse(service.is_available())
            self.assertIn("macOS", service.disabled_reason)

    def test_source_mode_is_unavailable(self):
        with patch.object(sys, "platform", "darwin"), patch.object(sys, "frozen", False, create=True):
            service = create_macos_updater_service()
            self.assertFalse(service.is_available())
            self.assertIn("Source builds", service.disabled_reason)

    def test_managed_mode_is_unavailable(self):
        with patch.object(sys, "platform", "darwin"), patch.object(sys, "frozen", True, create=True):
            with patch("diffasaurus.ui.macos_updater.managed_updates_enabled", return_value=True):
                service = create_macos_updater_service()
                self.assertFalse(service.is_available())
                self.assertEqual(service.status_message(), MANAGED_UPDATES_MESSAGE)

    def test_missing_sparkle_is_gracefully_disabled(self):
        with patch.object(sys, "platform", "darwin"), patch.object(sys, "frozen", True, create=True):
            with patch("diffasaurus.ui.macos_updater.managed_updates_enabled", return_value=False):
                with patch("diffasaurus.ui.macos_updater.updates_enabled", return_value=True):
                    with patch("diffasaurus.ui.macos_updater.sparkle_framework_path", return_value=None):
                        service = create_macos_updater_service()
                        self.assertFalse(service.is_available())
                        self.assertIn("Sparkle.framework", service.disabled_reason)

    def test_check_action_reports_source_mode_message(self):
        with patch.object(sys, "platform", "darwin"), patch.object(sys, "frozen", False, create=True):
            service = create_macos_updater_service()
            ok, message = service.check_for_updates()
            self.assertFalse(ok)
            self.assertIn("packaged macOS app", message)

    def test_save_callback_invoked_on_delegate_install_hook(self):
        callback = MagicMock()
        delegate_holder = {}

        fake_controller = MagicMock()
        fake_controller.canCheckForUpdates.return_value = True

        class FakeControllerClass:
            def alloc(self):
                return self

            def initWithStartingUpdater_updaterDelegate_userDriverDelegate_(
                self, starting, updater_delegate, user_driver_delegate
            ):
                delegate_holder["delegate"] = updater_delegate
                return fake_controller

        class FakeNSObject:
            def __init__(self):
                pass

            @classmethod
            def alloc(cls):
                instance = cls()
                instance.init = lambda: instance
                return instance

        fake_objc = MagicMock()
        fake_objc.lookUpClass.return_value = FakeControllerClass()

        fake_foundation = MagicMock()
        fake_foundation.NSObject = FakeNSObject

        with patch.object(sys, "platform", "darwin"), patch.object(sys, "frozen", True, create=True):
            with patch("diffasaurus.ui.macos_updater.managed_updates_enabled", return_value=False):
                with patch("diffasaurus.ui.macos_updater.updates_enabled", return_value=True):
                    with patch("diffasaurus.ui.macos_updater.sparkle_framework_path", return_value=Path("/Sparkle.framework")):
                        with patch("diffasaurus.ui.macos_updater._info_plist_value", side_effect=lambda key: "configured"):
                            with patch.dict(sys.modules, {"objc": fake_objc, "Foundation": fake_foundation}):
                                service = MacOsUpdaterService(save_session_callback=callback)
                                delegate = delegate_holder["delegate"]
                                delegate.updater_willInstallUpdate_(None, None)
                                callback.assert_called_once()

    def test_strong_references_retained(self):
        fake_controller = MagicMock()
        fake_controller.canCheckForUpdates.return_value = True

        class FakeControllerClass:
            def alloc(self):
                return self

            def initWithStartingUpdater_updaterDelegate_userDriverDelegate_(
                self, starting, updater_delegate, user_driver_delegate
            ):
                return fake_controller

        class FakeNSObject:
            @classmethod
            def alloc(cls):
                instance = cls()
                instance.init = lambda: instance
                return instance

        fake_objc = MagicMock()
        fake_objc.lookUpClass.return_value = FakeControllerClass()
        fake_foundation = MagicMock()
        fake_foundation.NSObject = FakeNSObject

        with patch.object(sys, "platform", "darwin"), patch.object(sys, "frozen", True, create=True):
            with patch("diffasaurus.ui.macos_updater.managed_updates_enabled", return_value=False):
                with patch("diffasaurus.ui.macos_updater.updates_enabled", return_value=True):
                    with patch("diffasaurus.ui.macos_updater.sparkle_framework_path", return_value=Path("/Sparkle.framework")):
                        with patch("diffasaurus.ui.macos_updater._info_plist_value", side_effect=lambda key: "configured"):
                            with patch.dict(sys.modules, {"objc": fake_objc, "Foundation": fake_foundation}):
                                service = MacOsUpdaterService()
                                self.assertIsNotNone(service._controller)
                                self.assertIsNotNone(service._updater_delegate)

    def test_transient_install_detection(self):
        bundle = Path("/Volumes/Diffasaurus/Diffasaurus.app")
        with patch("diffasaurus.ui.macos_updater._app_bundle_path", return_value=bundle):
            self.assertTrue(running_from_transient_install_location())

    def test_packaged_detection_requires_frozen(self):
        with patch.object(sys, "platform", "darwin"), patch.object(sys, "frozen", False, create=True):
            self.assertFalse(is_packaged_macos_app())


class MacOsUpdaterMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_check_for_updates_hidden_in_source_mode(self):
        from diffasaurus.ui.main_window import DiffasaurusWindow

        with tempfile.TemporaryDirectory() as directory:
            with patch("diffasaurus.ui.main_window.get_active_reports_dir", return_value=Path(directory)):
                with patch.object(sys, "platform", "darwin"):
                    with patch.object(DiffasaurusWindow, "refresh_history", lambda self: None):
                        window = DiffasaurusWindow()
                        window._screen_fitted = True
                        self.assertIsNotNone(window._check_updates_action)
                        self.assertFalse(window._check_updates_action.isVisible())
                        window.close()
                        window.thread_pool.waitForDone(2_000)

    def test_managed_mode_hides_menu_action(self):
        from diffasaurus.ui.main_window import DiffasaurusWindow

        with tempfile.TemporaryDirectory() as directory:
            with patch("diffasaurus.ui.main_window.get_active_reports_dir", return_value=Path(directory)):
                with patch.object(sys, "platform", "darwin"):
                    with patch("diffasaurus.ui.main_window.managed_updates_enabled", return_value=True):
                        with patch.object(DiffasaurusWindow, "refresh_history", lambda self: None):
                            window = DiffasaurusWindow()
                            window._screen_fitted = True
                            self.assertFalse(window._macos_updater.is_available())
                            self.assertFalse(window._check_updates_action.isVisible())
                            window.close()
                            window.thread_pool.waitForDone(2_000)

    def test_packaged_updater_action_uses_application_specific_role(self):
        from PyQt6.QtGui import QAction
        from diffasaurus.ui.main_window import DiffasaurusWindow

        fake_updater = MagicMock()
        fake_updater.is_available.return_value = True
        fake_updater.can_check_for_updates.return_value = True

        with tempfile.TemporaryDirectory() as directory:
            with patch("diffasaurus.ui.main_window.get_active_reports_dir", return_value=Path(directory)):
                with patch.object(sys, "platform", "darwin"):
                    with patch.object(DiffasaurusWindow, "refresh_history", lambda self: None):
                        with patch(
                            "diffasaurus.ui.main_window.create_macos_updater_service",
                            return_value=fake_updater,
                        ):
                            window = DiffasaurusWindow()
                            action = window._check_updates_action
                            host_menu = window._update_menu
                            menu_bar = window.menuBar()
                            self.assertIsNotNone(action)
                            self.assertIsNotNone(host_menu)
                            self.assertEqual(
                                action.menuRole(),
                                QAction.MenuRole.ApplicationSpecificRole,
                            )
                            self.assertIn(action, host_menu.actions())
                            self.assertIn(host_menu.menuAction(), menu_bar.actions())
                            self.assertNotIn(action, menu_bar.actions())
                            self.assertTrue(action.isVisible())
                            self.assertTrue(action.isEnabled())
                            window.close()
                            window.thread_pool.waitForDone(2_000)

    def test_update_action_visible_but_disabled_when_check_unavailable(self):
        from diffasaurus.ui.main_window import DiffasaurusWindow

        fake_updater = MagicMock()
        fake_updater.is_available.return_value = True
        fake_updater.can_check_for_updates.return_value = False

        with tempfile.TemporaryDirectory() as directory:
            with patch("diffasaurus.ui.main_window.get_active_reports_dir", return_value=Path(directory)):
                with patch.object(sys, "platform", "darwin"):
                    with patch.object(DiffasaurusWindow, "refresh_history", lambda self: None):
                        with patch(
                            "diffasaurus.ui.main_window.create_macos_updater_service",
                            return_value=fake_updater,
                        ):
                            window = DiffasaurusWindow()
                            self.assertTrue(window._check_updates_action.isVisible())
                            self.assertFalse(window._check_updates_action.isEnabled())
                            window.close()
                            window.thread_pool.waitForDone(2_000)

    def test_unavailable_sparkle_hides_menu_action(self):
        from diffasaurus.ui.main_window import DiffasaurusWindow

        fake_updater = MagicMock()
        fake_updater.is_available.return_value = False
        fake_updater.can_check_for_updates.return_value = False

        with tempfile.TemporaryDirectory() as directory:
            with patch("diffasaurus.ui.main_window.get_active_reports_dir", return_value=Path(directory)):
                with patch.object(sys, "platform", "darwin"):
                    with patch.object(DiffasaurusWindow, "refresh_history", lambda self: None):
                        with patch(
                            "diffasaurus.ui.main_window.create_macos_updater_service",
                            return_value=fake_updater,
                        ):
                            window = DiffasaurusWindow()
                            self.assertFalse(window._check_updates_action.isVisible())
                            window.close()
                            window.thread_pool.waitForDone(2_000)

    def test_triggering_menu_action_calls_check_for_updates(self):
        from diffasaurus.ui.main_window import DiffasaurusWindow

        fake_updater = MagicMock()
        fake_updater.is_available.return_value = True
        fake_updater.can_check_for_updates.return_value = True
        fake_updater.check_for_updates.return_value = (True, "")

        with tempfile.TemporaryDirectory() as directory:
            with patch("diffasaurus.ui.main_window.get_active_reports_dir", return_value=Path(directory)):
                with patch.object(sys, "platform", "darwin"):
                    with patch.object(DiffasaurusWindow, "refresh_history", lambda self: None):
                        with patch(
                            "diffasaurus.ui.main_window.create_macos_updater_service",
                            return_value=fake_updater,
                        ):
                            window = DiffasaurusWindow()
                            window._check_for_updates()
                            fake_updater.check_for_updates.assert_called_once()
                            window.close()
                            window.thread_pool.waitForDone(2_000)


if __name__ == "__main__":
    unittest.main()
