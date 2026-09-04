import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtCore import QUrl

app = QApplication(sys.argv)
player = QMediaPlayer()
audio = QAudioOutput()
player.setAudioOutput(audio)
widget = QVideoWidget()
player.setVideoOutput(widget)

def on_status(status):
    print("Media status:", status)

def on_error(err, desc):
    print("Media error:", err, desc)

player.mediaStatusChanged.connect(on_status)
player.errorOccurred.connect(on_error)

player.setSource(QUrl.fromLocalFile("samples/sample1.mp4"))
print("Playing sample1...")
player.play()

# 1秒だけイベントループを回して確認
import time
start = time.time()
while time.time() - start < 1.5:
    app.processEvents()
    time.sleep(0.05)

print("Playback test finished.")
