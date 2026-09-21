#!/bin/bash
# Mac: double-click this file (first time: right-click -> Open). Or in Terminal:  ./start.command
cd "$(dirname "$0")" || exit 1

if ! command -v docker >/dev/null 2>&1; then
  echo
  echo " Docker is not installed."
  echo " Install 'Docker Desktop' from https://www.docker.com/products/docker-desktop/ then run this again."
  read -r -p " Press Enter to close..." _; exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo
  echo " Docker is installed but not running. Opening Docker Desktop - wait until it says it is running,"
  echo " then run this again."
  open -a Docker 2>/dev/null
  read -r -p " Press Enter to close..." _; exit 1
fi

mkdir -p data out
echo
echo " Building and starting. The FIRST time takes a few minutes; after that it starts in seconds."
echo
if ! docker compose up --build -d; then
  echo
  echo " Something went wrong while building. Scroll up for the error and send it to the team."
  read -r -p " Press Enter to close..." _; exit 1
fi

echo " Waiting for the app to be ready..."
for _ in $(seq 1 60); do
  curl -sf http://localhost:8501/_stcore/health >/dev/null 2>&1 && ready=1 && break
  sleep 2
done
if [ -z "$ready" ]; then
  echo " The app did not answer in time. Check it with:  docker compose logs"
  read -r -p " Press Enter to close..." _; exit 1
fi

IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}')
echo
echo " ==========================================================="
echo "  READY"
echo "  On this computer:  http://localhost:8501"
[ -n "$IP" ] && echo "  From a phone on the same Wi-Fi:  http://$IP:8501"
echo " ==========================================================="
echo
(open http://localhost:8501 2>/dev/null || xdg-open http://localhost:8501 2>/dev/null) &
echo " To stop it later:  ./stop.command"
read -r -p " Press Enter to close this window (the app keeps running)..." _
