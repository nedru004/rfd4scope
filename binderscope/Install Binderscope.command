#!/bin/bash
# Double-click this file to install Binderscope. It opens a Terminal window, runs install.sh and waits for you to close it.
cd "$(dirname "$0")" || exit 1
./install.sh "$@"
status=$?
echo
read -r -p "Press Return to close this window. " _
exit $status
