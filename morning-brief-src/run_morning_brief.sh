#!/bin/bash
# 晨报一键生成 —— 定时任务调用这个
# 用法：bash run_morning_brief.sh
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

LOG="$HERE/out/run.log"
mkdir -p "$HERE/out" "$HERE/site"

{
  echo "===== 晨报生成 $(date '+%Y-%m-%d %H:%M:%S') ====="

  # 0) 刷新补库工作台快照（库存/动销每天变，必须用最新）
  #    写取件单 + 下载（若会话侧已把 export url 写进 .cache/url.txt）
  python3 refresh_replenish.py || true

  # 1) 取数：云端 state.json 作环比基准 → 本地重跑 scrape.py 拿当日行情 + 次日价
  if ! python3 fetch_data.py; then
    echo "!! fetch_data.py 失败，退回云端数据模式"
    python3 fetch_data.py --fallback || { echo "!! 数据获取彻底失败"; exit 2; }
  fi

  # 2) 渲染（build.py 会同时写 out/morning_brief.html 与 site/index.html）
  if ! python3 build.py; then
    echo "!! build.py 失败"
    exit 3
  fi

  echo "===== 完成 $(date '+%H:%M:%S') ====="
} >> "$LOG" 2>&1

# 把结论打到 stdout，方便自动化直接读取
if [ -f "$HERE/out/morning_brief.txt" ]; then
  echo "OK"
  head -3 "$HERE/out/morning_brief.txt"
else
  echo "FAIL"
  tail -20 "$LOG"
  exit 4
fi
