"""Run this with PowerPoint open and F5 slide show active to diagnose COM state."""
import win32com.client

try:
    app = win32com.client.GetActiveObject("PowerPoint.Application")
    print(f"Connected to PowerPoint: {app.Name}")
    print(f"Presentations open: {app.Presentations.Count}")
    print(f"SlideShowWindows count: {app.SlideShowWindows.Count}")

    if app.Presentations.Count > 0:
        pres = app.ActivePresentation
        print(f"Active presentation: {pres.Name}")
        print(f"Slides: {pres.Slides.Count}")

    for i in range(app.SlideShowWindows.Count):
        view = app.SlideShowWindows(1).View
        print(f"Current slide position: {view.CurrentShowPosition}")
        print("Attempting Next()...")
        view.Next()
        print("Next() succeeded.")
    else:
        print("No slide show window found — press F5 in PowerPoint first.")

except Exception as e:
    print(f"Error: {e}")
