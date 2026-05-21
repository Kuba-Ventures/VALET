#!/bin/bash
# JARVIS self-restart — spawns a detached restarter so the act of killing
# the current JARVIS process doesn't kill the restarter.
#
# The detached child:
#   1. Sleeps 2s so the caller can speak/log/clean up.
#   2. Either kicks launchctl (if launchd-supervised) OR pkills + nohups
#      a fresh server.py.
#   3. Logs to data/logs/restart.log.
#
# Caller (the JARVIS Python process) should exit cleanly after spawning
# this script. launchd will respawn it automatically when supervised; the
# nohup branch handles the dev case.
set -e
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
LOG="$ROOT/data/logs/restart.log"
mkdir -p "$(dirname "$LOG")"

UID_NUM="${UID:-$(id -u)}"

# Detach via nohup+setsid so the restarter survives parent death.
# Use a here-doc'd inner script so the variables expand HERE in the parent's
# context (not in the child, which won't have $ROOT in scope cleanly).
nohup setsid bash -c "
  sleep 2
  echo \"[\$(date '+%Y-%m-%dT%H:%M:%S%z')] restart triggered (pid=\$\$)\" >> '$LOG'
  if launchctl list 2>/dev/null | grep -q com.jarvis.backend; then
    launchctl kickstart -k 'gui/$UID_NUM/com.jarvis.backend' >> '$LOG' 2>&1
    echo \"[\$(date '+%Y-%m-%dT%H:%M:%S%z')] kickstart sent to launchd\" >> '$LOG'
  else
    pkill -f 'python.*server.py' >> '$LOG' 2>&1 || true
    sleep 1
    cd '$ROOT'
    nohup .venv/bin/python server.py </dev/null \\
        >>'$ROOT/logs/jarvis.out.log' \\
        2>>'$ROOT/logs/jarvis.err.log' &
    echo \"[\$(date '+%Y-%m-%dT%H:%M:%S%z')] nohup-relaunched server (pid=\$!)\" >> '$LOG'
  fi
" </dev/null >/dev/null 2>&1 &
disown

echo "Restarter spawned (background pid $!). Parent should now exit."
