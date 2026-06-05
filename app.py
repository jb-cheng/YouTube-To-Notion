"""Tkinter desktop app to turn a YouTube transcript into a Notion page.

Entry point — delegates to ui.YouTubeToNotionApp.
"""

from ui import YouTubeToNotionApp


def main() -> None:
    app = YouTubeToNotionApp()
    app.mainloop()


if __name__ == "__main__":
    main()
