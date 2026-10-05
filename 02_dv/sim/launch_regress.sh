#!/bin/bash
cd /home/claude/neo
setsid nohup bash sim/regress_full.sh > logs/regress_full.log 2>&1 < /dev/null &
echo $! > logs/regress.pid
