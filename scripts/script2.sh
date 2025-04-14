#!/bin/bash

echo "Bash script2 starting..."
echo "User: $(whoami)"
echo "Current directory: $(pwd)"
echo "Listing files in /tmp:"
ls -l /tmp
sleep 2

echo "Attempting a command that might need sudo (listing /root)..."
# This command will likely fail without proper sudoers setup if run by a non-root user
# If sudo is configured, it will run without a password prompt.
sudo ls -l /root
EXIT_CODE=$? # Capture exit code of the sudo command

if [ $EXIT_CODE -ne 0 ]; then
  echo "WARNING: 'sudo ls -l /root' failed. Did you configure sudoers?" >&2 # Write to stderr
fi

sleep 1
echo "Generating some more output..."
for i in {1..3}; do
  echo "Line $i"
  sleep 0.5
done

echo "Script finished."
exit 0 # Ensure a clean exit