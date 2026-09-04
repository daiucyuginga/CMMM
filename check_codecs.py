import sys
import time
from PySide6.QtWidgets import QApplication
from PySide6.QtWebEngineWidgets import QWebEngineView

app = QApplication(sys.argv)
view = QWebEngineView()

def callback(res):
    print("CanPlayType mp4/h264 result:", repr(res))
    app.quit()

def on_load(ok):
    js = "document.createElement('video').canPlayType('video/mp4; codecs=\"avc1.42E01E, mp4a.40.2\"')"
    view.page().runJavaScript(js, callback)

view.loadFinished.connect(on_load)
view.setHtml("<html><body><h1>Test</h1></body></html>")

app.exec()
