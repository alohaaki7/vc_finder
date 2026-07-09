#!/bin/bash
# setup_launchd.sh
# Creates and loads a macOS launchd Agent to run the VC lead finder pipeline
# automatically every Monday at 9:00 AM.
# Bypasses macOS cron "Operation not permitted" sandboxing issues.

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PLIST_PATH="$HOME/Library/LaunchAgents/com.vcleadfinder.weekly.plist"
PYTHON_PATH=$(which python3)

echo "=========================================================="
echo "Installing macOS launchd Agent for VC Lead Finder"
echo "=========================================================="

if [ -z "$PYTHON_PATH" ]; then
    echo "❌ Error: python3 could not be found. Please install python3."
    exit 1
fi

echo "✓ Found python3 at: $PYTHON_PATH"
echo "✓ Project directory: $SCRIPT_DIR"

# Generate plist file
cat <<EOF > "$PLIST_PATH"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.vcleadfinder.weekly</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON_PATH</string>
        <string>$SCRIPT_DIR/pipeline.py</string>
        <string>--days</string>
        <string>7</string>
        <string>--type</string>
        <string>vc</string>
        <string>--output</string>
        <string>$SCRIPT_DIR/ALL_VC_LEADS.csv</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>9</integer>
        <key>Minute</key>
        <integer>0</integer>
        <key>Weekday</key>
        <integer>1</integer> <!-- 1 is Monday in launchd -->
    </dict>
    <key>StandardOutPath</key>
    <string>$SCRIPT_DIR/launchd_output.log</string>
    <key>StandardErrorPath</key>
    <string>$SCRIPT_DIR/launchd_error.log</string>
    <key>WorkingDirectory</key>
    <string>$SCRIPT_DIR</string>
</dict>
</plist>
EOF

echo "✓ Created plist file at: $PLIST_PATH"

# Unload previous instance if it exists
launchctl unload "$PLIST_PATH" 2>/dev/null

# Load the new plist
if launchctl load -w "$PLIST_PATH"; then
    echo "✓ Successfully loaded LaunchAgent 'com.vcleadfinder.weekly'"
    echo "✓ The pipeline will now run automatically every Monday at 9:00 AM."
    echo "✓ Logs will be written to:"
    echo "  - Standard Output: $SCRIPT_DIR/launchd_output.log"
    echo "  - Standard Error:  $SCRIPT_DIR/launchd_error.log"
else
    echo "❌ Error: Failed to load LaunchAgent with launchctl."
    exit 1
fi

echo "=========================================================="
echo "Installation complete!"
echo "=========================================================="
