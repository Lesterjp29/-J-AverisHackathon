#!/bin/bash
cd "$(dirname "$0")" || exit 1
docker compose down
echo "Stopped."
read -r -p "Press Enter to close..." _
