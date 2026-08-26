#!/bin/bash
set -e

echo ""
echo "  ╔══════════════════════════════════════╗"
echo "  ║       OptiLoop Setup (Linux/macOS)   ║"
echo "  ╚══════════════════════════════════════╝"
echo ""

# --- Check Docker ---
if command -v docker &> /dev/null; then
    echo "  ✔ Docker found: $(docker --version | head -1)"
else
    echo "  ✘ Docker not found."
    echo "    Install from: https://docs.docker.com/get-docker/"
    echo "    OptiLoop needs Docker to run agent sandboxes."
    read -p "    Continue without Docker? (y/N): " yn
    if [[ ! "$yn" =~ ^[Yy]$ ]]; then exit 1; fi
fi

# --- Check Python ---
if ! command -v python3 &> /dev/null; then
    echo "  ✘ python3 not found. Install Python 3.11+ first."
    exit 1
fi
echo "  ✔ Python found: $(python3 --version 2>&1)"

# --- API Key ---
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo ""
    echo "  Set your OpenRouter API key:"
    echo "  (Get one free at https://openrouter.ai/keys)"
    read -p "  OPENROUTER_API_KEY: " api_key
    if [ -n "$api_key" ]; then
        sed -i "s|your_openrouter_api_key_here|$api_key|" .env
        echo "  ✔ API key saved to .env"
    else
        echo "  ⚠ No key entered. Edit .env manually later."
    fi
else
    echo "  ✔ .env already exists"
fi

# --- Install Python deps ---
echo ""
echo "  Installing Python dependencies..."
pip install -r requirements.txt -q
echo "  ✔ Dependencies installed"

# --- Run tests ---
echo ""
echo "  Running test suite..."
pytest tests/ -q --tb=line
echo ""

# --- Docker Compose ---
if command -v docker &> /dev/null && command -v docker &> /dev/null; then
    read -p "  Start with Docker Compose? (Y/n): " dc
    if [[ ! "$dc" =~ ^[Nn]$ ]]; then
        echo "  Building and starting services..."
        docker compose up -d --build
        echo ""
        echo "  ✔ Backend: http://localhost:8000"
        echo "  ✔ Dashboard: http://localhost:3000"
    fi
fi

echo ""
echo "  ╔══════════════════════════════════════╗"
echo "  ║         Setup Complete!              ║"
echo "  ╚══════════════════════════════════════╝"
echo ""
