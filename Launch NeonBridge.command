#!/bin/zsh
cd "${0:A:h}"
if [[ ! -x .venv/bin/python ]]; then
  print 'Run the setup instructions in readme.md first.'
  read '?Press Enter to close'
  exit 1
fi
exec .venv/bin/python run.py
