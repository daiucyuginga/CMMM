"""
generate_test_videos.py
テスト用の動画ファイルを生成するスクリプト
"""

import os
import subprocess
from core.ffmpeg_tools import find_ffmpeg_binaries

ffmpeg_path, _ = find_ffmpeg_binaries()
if not ffmpeg_path:
    print("FFmpeg not found!")
    exit(1)

os.makedirs("samples", exist_ok=True)

sample1 = os.path.abspath("samples/sample1.mp4")
sample2 = os.path.abspath("samples/sample2.mp4")
sample_diff = os.path.abspath("samples/sample_incompatible.mp4")

# sample1: 1920x1080, 30fps, 10s, h264/aac
cmd1 = [
    ffmpeg_path, "-y",
    "-f", "lavfi", "-i", "testsrc=size=1920x1080:rate=30:duration=10",
    "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "30",
    "-c:a", "aac", "-ar", "48000", "-b:a", "128k",
    sample1
]

# sample2: 1920x1080, 30fps, 10s, h264/aac (同スペック)
cmd2 = [
    ffmpeg_path, "-y",
    "-f", "lavfi", "-i", "smptebars=size=1920x1080:rate=30:duration=10",
    "-f", "lavfi", "-i", "sine=frequency=880:duration=10",
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "30",
    "-c:a", "aac", "-ar", "48000", "-b:a", "128k",
    sample2
]

# sample_diff: 1280x720 (異なる解像度)
cmd3 = [
    ffmpeg_path, "-y",
    "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30:duration=5",
    "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
    "-c:v", "libx264", "-pix_fmt", "yuv420p",
    "-c:a", "aac", "-ar", "48000",
    sample_diff
]

print("Generating sample 1...")
subprocess.run(cmd1, check=True)
print("Generating sample 2...")
subprocess.run(cmd2, check=True)
print("Generating incompatible sample...")
subprocess.run(cmd3, check=True)
print("Test videos generated successfully in samples/")
