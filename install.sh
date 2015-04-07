#!/bin/bash

# Define GitHub repository details
REPO="iitmcvg/git-agent"
BRANCH="main"  # Adjust branch name as necessary

# Step 1: Install the Python package from GitHub
echo "Installing Git Agent from GitHub..."
pip install git+https://github.com/$REPO.git@$BRANCH

# Step 2: Download the config.toml file into /etc/git-agent/
echo "Setting up configuration..."
mkdir -p /etc/git-agent
curl -sL https://raw.githubusercontent.com/$REPO/$BRANCH/setup/templates/config.toml > /etc/git-agent/config.toml
chmod 666 /etc/git-agent/config.toml  # Readable and editable by all users

# Step 3: Create /opt/git-agent/ directory and set permissions
echo "Creating application directory..."
mkdir -p /opt/git-agent
chmod -R 777 /opt/git-agent  # Readable and writable by all users

# Step 4: Download the systemd service file
echo "Setting up systemd service..."
curl -sL https://raw.githubusercontent.com/$REPO/$BRANCH/setup/git-agent.service > /etc/systemd/system/git-agent.service

# Step 5: Load and start the systemd service
echo "Enabling and starting the Git Agent service..."
systemctl daemon-reload
systemctl enable git-agent.service
systemctl start git-agent.service

echo "Git Agent installation and setup complete!"
