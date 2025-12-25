#!/bin/bash
# Build Tailwind CSS for production
# Run: ./build-css.sh
# For development with watch mode: ./build-css.sh --watch

if [ ! -f "./tailwindcss" ]; then
    echo "Downloading Tailwind CSS CLI..."
    curl -sLO https://github.com/tailwindlabs/tailwindcss/releases/latest/download/tailwindcss-linux-x64
    chmod +x tailwindcss-linux-x64
    mv tailwindcss-linux-x64 tailwindcss
fi

if [ "$1" == "--watch" ]; then
    echo "Watching for changes..."
    ./tailwindcss -i ./static/css/tailwind.css -o ./static/css/tailwind.output.css --watch
else
    echo "Building CSS..."
    ./tailwindcss -i ./static/css/tailwind.css -o ./static/css/tailwind.output.css --minify
    echo "Done!"
fi
