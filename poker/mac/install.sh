#!/bin/bash
# Installs the poker RSVP helper on this Mac.
# Run:  curl -fsSL https://kylerbooth.com/poker/mac/install.sh | bash
set -e
DIR="$HOME/.poker-rsvp"
LABEL="com.kylerbooth.poker-rsvp"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
TTY=/dev/tty
say(){ printf "\n\033[1m%s\033[0m\n" "$1"; }
pause(){ printf "%s" "$1"; read -r _ < "$TTY"; }

say "Poker RSVP helper: installing"

# 1. Python (comes with Apple's command line tools)
if ! /usr/bin/python3 -c "print(1)" >/dev/null 2>&1; then
  say "Python isn't installed yet. A window will pop up to install Apple's command line tools."
  xcode-select --install 2>/dev/null || true
  echo "When that install finishes, run the install command again."
  exit 1
fi
PY="$(/usr/bin/python3 -c 'import os,sys;print(os.path.realpath(sys.executable))')"

# 2. Download the script
mkdir -p "$DIR"
curl -fsSL "https://kylerbooth.com/poker/mac/poker_rsvp.py?t=$(date +%s)" -o "$DIR/poker_rsvp.py"
chmod +x "$DIR/poker_rsvp.py"
echo "Downloaded to $DIR"

# 3. Terminal needs Full Disk Access to read Messages for setup
if ! /usr/bin/python3 -c "open('$HOME/Library/Messages/chat.db','rb').read(16)" >/dev/null 2>&1; then
  say "Step 1 of 2: let Terminal read Messages"
  echo "System Settings is opening to Full Disk Access."
  echo "  • Turn on Terminal (click + and add Applications > Utilities > Terminal if it isn't listed)."
  echo "  • Then QUIT Terminal completely (Cmd+Q), reopen it, and run the install command again."
  open "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"
  exit 1
fi

# 4. Pick the group chat and confirm names
say "Setup: pick the poker group chat and confirm names"
/usr/bin/python3 "$DIR/poker_rsvp.py" setup < "$TTY"

# 5. Run every 5 minutes in the background
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$DIR/poker_rsvp.py</string><string>run</string></array>
  <key>StartInterval</key><integer>300</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$DIR/background.log</string>
  <key>StandardErrorPath</key><string>$DIR/background.log</string>
</dict></plist>
EOF
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

# 6. The background job needs its own Full Disk Access (Python, not Terminal)
say "Step 2 of 2: let the background job read Messages"
echo "A Finder window will show the Python program the job uses, and Full Disk Access will open."
echo "  • Drag that Python file from Finder into the Full Disk Access list, and make sure it's switched on."
open -R "$PY"
open "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"
pause "Press Enter here once it's added… "
: > "$DIR/background.log"
launchctl kickstart -k "gui/$(id -u)/$LABEL" 2>/dev/null || true
sleep 6
if grep -q "Can't read your Messages" "$DIR/background.log" 2>/dev/null; then
  say "The background job still can't read Messages."
  echo "Add this file to Full Disk Access and switch it on:  $PY"
  echo "Then run:  launchctl kickstart -k gui/$(id -u)/$LABEL"
  exit 1
fi

say "All set ✓"
echo "Every 5 minutes, in the 3 days before a game, it reads replies to your reminder text"
echo "in the poker chat and RSVPs people on kylerbooth.com/poker."
echo "  • Check what it sees:   /usr/bin/python3 ~/.poker-rsvp/poker_rsvp.py status"
echo "  • Activity log:         ~/.poker-rsvp/log.txt"
echo "  • Re-match names:       /usr/bin/python3 ~/.poker-rsvp/poker_rsvp.py setup"
echo "  • Uninstall:            launchctl bootout gui/$(id -u)/$LABEL; rm -rf ~/.poker-rsvp \"$PLIST\""
