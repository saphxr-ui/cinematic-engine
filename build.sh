#!/usr/bin/env bash
# build.sh - runs on Render during build phase
set -e

echo "Installing Python dependencies..."
pip install -r requirements.txt

echo "Installing FFmpeg..."
apt-get update -qq && apt-get install -y ffmpeg

echo "Build complete ✅"
