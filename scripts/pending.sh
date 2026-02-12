#!/usr/bin/env bash
# 提交待处理 Alpha
cd "$(dirname "$0")/.." || exit 1
python main.py pending
