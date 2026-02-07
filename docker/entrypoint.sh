#!/bin/bash
set -e

echo "=========================================="
echo "Kiro Gateway Starting..."
echo "=========================================="
echo ""

# Check if running in multi-tenant mode
if [ -n "$ENCRYPTION_KEY" ] && [ -n "$ADMIN_SESSION_SECRET" ]; then
    echo "Multi-tenant mode detected"
    echo "Accounts are managed via admin UI at /admin"
    echo ""

    # Initialize database directory
    mkdir -p /app/data

    # Start Kiro Gateway directly
    echo "Starting Kiro Gateway..."
    echo "Service: http://localhost:8000"
    echo "Admin UI: http://localhost:8000/admin/login"
    echo "=========================================="
    echo ""
    exec python main.py
fi

# Legacy single-account mode below
# Check for existing credentials
KIRO_CLI_DB="/root/.local/share/kiro-cli/data.sqlite3"

if [ -f "$KIRO_CLI_DB" ]; then
    echo "Found existing kiro-cli credentials"
    echo "To re-login, delete Docker volume: docker-compose down -v"
    echo ""
else
    echo "First startup, kiro-cli login required"
    echo "=========================================="
    echo ""
    echo "Steps:"
    echo "1. Copy the login URL shown below"
    echo "2. Open the URL in your browser"
    echo "3. Complete AWS Builder ID or enterprise login"
    echo "4. After login, Kiro Gateway will start automatically"
    echo ""
    echo "=========================================="
    echo ""

    # Check login parameters
    if [ -z "$KIRO_START_URL" ] || [ -z "$KIRO_LOGIN_REGION" ]; then
        echo "Error: Missing login parameters"
        echo ""
        echo "Set these environment variables:"
        echo "  KIRO_START_URL=\"https://amzn.awsapps.com/start\""
        echo "  KIRO_LOGIN_REGION=\"us-east-1\""
        echo ""
        exit 1
    fi

    # Set license (default: pro)
    export LICENSE="${KIRO_LICENSE:-pro}"
    export KIRO_START_URL
    export KIRO_LOGIN_REGION

    echo "Login config:"
    echo "   Start URL: $KIRO_START_URL"
    echo "   Region: $KIRO_LOGIN_REGION"
    echo "   License: $LICENSE"
    echo ""

    if command -v expect >/dev/null 2>&1; then
        expect << 'EOF'
set timeout 300
set start_url $env(KIRO_START_URL)
set region $env(KIRO_LOGIN_REGION)
set license $env(LICENSE)

spawn kiro-cli login --license=$license --use-device-flow

expect {
    "Select login method" {
        send "Use with IDC Account\r"
        exp_continue
    }
    "Enter Start URL" {
        send "$start_url\r"
    }
    timeout {
        puts "\nError: Login prompt timeout"
        exit 1
    }
}

expect {
    "Enter Region" {
        send "$region\r"
    }
    timeout {
        puts "\nError: Region prompt timeout"
        exit 1
    }
}

expect {
    "Logged in successfully" {
        puts "\nLogin confirmed!"
        exp_continue
    }
    "Device authorized" {
        exp_continue
    }
    "Open this URL:" {
        exp_continue
    }
    "Confirm the following code" {
        exp_continue
    }
    eof {
        puts "\nkiro-cli login complete"
        exit 0
    }
    timeout {
        puts "\nError: Login timeout (5 minutes)"
        puts "Make sure you completed browser authorization"
        exit 1
    }
}
EOF

        if [ $? -ne 0 ]; then
            echo ""
            echo "Login failed!"
            echo ""
            echo "Check:"
            echo "  1. Start URL is correct"
            echo "  2. Region is correct"
            echo "  3. Browser authorization was completed"
            echo ""
            exit 1
        fi
    else
        echo "Warning: expect not installed, using interactive login"
        kiro-cli login --license=$LICENSE
    fi

    echo ""
    echo "=========================================="
    echo "Login successful!"
    echo "=========================================="
    echo ""
fi

# Start Kiro Gateway
echo "Starting Kiro Gateway..."
echo "Service: http://localhost:8000"
echo "=========================================="
echo ""

exec python main.py
