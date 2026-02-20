#!/bin/bash

# YouTube Music Downloader - Stop Script

set -e

echo "Stopping YouTube Music Downloader..."
docker-compose down

echo ""
echo "✅ Application stopped"
echo ""
echo "To start again: ./start.sh"
echo "To remove all data: docker-compose down -v"
