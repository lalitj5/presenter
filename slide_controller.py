import time
import win32con
import win32gui


class SlideController:
    """
    Controls a running PowerPoint presentation.

    Primary path: win32com — attaches to an already-open PowerPoint instance
    and sends commands directly to the SlideShowView. Requires pywin32.

    Fallback path: pyautogui — sends keyboard arrow keys to the focused window.
    Used when PowerPoint is not running or win32com is unavailable. Good for
    testing the pipeline without a live deck.

    Usage:
        controller = SlideController()
        ok = controller.connect()
        controller.advance()
    """

    def __init__(self):
        self._ppt_app = None        # win32com Application object
        self._presentation = None   # active Presentation object
        self._shell = None          # WScript.Shell for SendKeys
        self._using_fallback = False

    def connect(self) -> bool:
        """
        Attempt to attach to the running PowerPoint instance.
        Returns True if connected via COM, False if falling back to pyautogui.
        """
        try:
            import win32com.client
            self._ppt_app = win32com.client.GetActiveObject("PowerPoint.Application")
            self._presentation = self._ppt_app.ActivePresentation
            self._shell = win32com.client.Dispatch("WScript.Shell")
            self._using_fallback = False
            total = self._presentation.Slides.Count
            print(f"[SlideController] Attached to PowerPoint — {total} slides")
            return True
        except Exception as e:
            import win32com.client
            self._shell = win32com.client.Dispatch("WScript.Shell")
            self._using_fallback = True
            print(f"[SlideController] Could not attach to PowerPoint ({e})")
            print("[SlideController] Falling back to WScript.Shell SendKeys")
            return False

    def _send_key(self, key: str) -> bool:
        """
        Activate PowerPoint via WScript.Shell.AppActivate (brings it to
        foreground by title) then inject a real keystroke with SendKeys.
        This is more reliable than SetForegroundWindow + PostMessage because
        WScript handles the focus transfer internally.
        """
        if not self._shell:
            print("[SlideController] Shell not initialised — was connect() called?")
            return False
        activated = self._shell.AppActivate("PowerPoint")
        if not activated:
            print("[SlideController] AppActivate could not find a PowerPoint window.")
            return False
        time.sleep(0.05)   # brief pause for focus transfer to complete
        self._shell.SendKeys(key)
        return True

    def _get_show_view(self):
        """
        Returns the active SlideShowView from the app-level SlideShowWindows
        collection. More reliable than going through the presentation object,
        which can hold a stale reference if the show started after connect().
        Raises if no slide show is currently running.
        """
        if self._ppt_app.SlideShowWindows.Count == 0:
            raise RuntimeError("No slide show is running — press F5 in PowerPoint first.")
        return self._ppt_app.SlideShowWindows(1).View

    def advance(self) -> None:
        """
        Advance to the next slide. Uses WScript.Shell.SendKeys so transitions
        and click animations play normally. COM view.Next() is intentionally
        avoided — it skips animations entirely.
        """
        self._send_key("{RIGHT}")

    def go_back(self) -> None:
        """Go back one slide."""
        self._send_key("{LEFT}")

    def current_slide(self) -> int:
        """Return the 1-indexed current slide number. Returns -1 if unavailable."""
        if self._using_fallback:
            return -1
        try:
            return self._get_show_view().CurrentShowPosition
        except Exception:
            return -1

    def total_slides(self) -> int:
        """Return total slide count. Returns -1 if unavailable."""
        if self._using_fallback:
            return -1
        try:
            return self._presentation.Slides.Count
        except Exception:
            return -1

    def is_fallback(self) -> bool:
        return self._using_fallback
