#!/bin/bash

# YouTube Music Downloader - Start Script
# This script starts the application and provides helpful information

set -e

echo "================================================"
echo "YouTube Music Downloader"
echo "================================================"
echo ""

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "❌ Error: Docker is not running. Please start Docker first."
    exit 1
fi

# Check if Docker Compose is installed
if ! command -v docker-compose &> /dev/null; then
    echo "❌ Error: docker-compose is not installed."
    exit 1
fi

echo "✅ Docker is running"
echo ""

# Create downloads directory if it doesn't exist
mkdir -p downloads

# Copy environment files if they don't exist
if [ ! -f backend/.env ]; then
    cp backend/.env.example backend/.env
    echo "✅ Created backend/.env from example"
fi

if [ ! -f frontend/.env ]; then
    cp frontend/.env.example frontend/.env
    echo "✅ Created frontend/.env from example"
fi

echo ""
echo "🚀 Starting services..."
echo ""

# Build and start services
docker-compose up -d --build

echo ""
echo "⏳ Waiting for services to be healthy..."
sleep 5

# Check if services are running
if docker-compose ps | grep -q "Up"; then
    echo ""
    echo "================================================"
    echo "✅ Application started successfully!"
    echo "================================================"
    echo ""
    echo "📱 Frontend:  http://localhost:80"
    echo "🔧 Backend:   http://localhost:8000"
    echo "📚 API Docs:  http://localhost:8000/docs"
    echo ""
    echo "📁 Downloads folder: ./downloads"
    echo ""
    echo "To view logs:     docker-compose logs -f"
    echo "To stop:          docker-compose down"
    echo "To restart:       docker-compose restart"
    echo ""
    echo "================================================"
else
    echo ""
    echo "❌ Error: Services failed to start"
    echo "Check logs with: docker-compose logs"
fi
