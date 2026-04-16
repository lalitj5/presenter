import pyautogui


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
            self._using_fallback = False
            total = self._presentation.Slides.Count
            print(f"[SlideController] Attached to PowerPoint — {total} slides")
            return True
        except Exception as e:
            self._using_fallback = True
            print(f"[SlideController] Could not attach to PowerPoint ({e})")
            print("[SlideController] Falling back to pyautogui (arrow keys)")
            return False

    def advance(self) -> None:
        """Advance to the next slide."""
        if self._using_fallback:
            pyautogui.press("right")
            return
        try:
            view = self._presentation.SlideShowWindow.View
            view.Next()
        except Exception as e:
            print(f"[SlideController] advance() failed ({e}), retrying via pyautogui")
            pyautogui.press("right")

    def go_back(self) -> None:
        """Go back to the previous slide."""
        if self._using_fallback:
            pyautogui.press("left")
            return
        try:
            view = self._presentation.SlideShowWindow.View
            view.Previous()
        except Exception as e:
            print(f"[SlideController] go_back() failed ({e}), retrying via pyautogui")
            pyautogui.press("left")

    def current_slide(self) -> int:
        """Return the 1-indexed current slide number. Returns -1 if unavailable."""
        if self._using_fallback:
            return -1
        try:
            return self._presentation.SlideShowWindow.View.CurrentShowPosition
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
